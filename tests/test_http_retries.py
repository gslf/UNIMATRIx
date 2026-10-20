"""Transient provider limits preserve diagnostics, routing and resumable decisions."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from unimatrix.models import transport
from unimatrix.models.health import check_model
from unimatrix.models.transport import InfrastructureFailure
from unimatrix.polis.runner import decide

from .test_models import MODEL, Probe, mocked, reply


@pytest.fixture
def waits(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(transport.asyncio, "sleep", sleep)
    monkeypatch.setattr(transport.random, "uniform", lambda low, high: 0)
    return sleep


@pytest.mark.parametrize("status", [429, 503])
async def test_rate_limit_waits_then_repeats_exact_request(status, waits):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(status, headers={"Retry-After": "90"},
                                  json={"error": {"message": "temporarily rate limited"}})
        return reply(completion_tokens=12)

    config = dict(MODEL, endpoint="https://openrouter.ai/api",
                  openrouter={"provider": {"only": ["streamlake/fp8"], "allow_fallbacks": False}})
    policy = Probe(config, mocked(handler))
    try:
        raw, usage = await decide(policy, {"week": 14})
        assert raw == "{}" and requests[0] == requests[1]
        assert requests[1]["provider"]["only"] == ["streamlake/fp8"]
        waits.assert_awaited_once_with(90)
        assert usage["provider_http_retries"] == 1
        assert usage["provider_http_retry_wait_seconds"] == 90
    finally:
        await policy.close()


async def test_persistent_limit_is_bounded_and_preserves_redacted_reason(waits, monkeypatch, caplog):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "secret-provider-token")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {
            "message": "Provider returned error secret-provider-token",
            "metadata": {"provider_name": "StreamLake", "limit_source": "upstream_provider_shared_pool"},
        }})

    policy = Probe(dict(MODEL, api_key_env="TEST_PROVIDER_KEY"), mocked(handler))
    try:
        with pytest.raises(InfrastructureFailure) as caught:
            await decide(policy, {})
        assert len(calls) == len(transport.HTTP_RETRY_BACKOFF) + 1
        assert [call.args[0] for call in waits.await_args_list] == list(transport.HTTP_RETRY_BACKOFF)
        assert not caught.value.retryable and caught.value.http_status == 429
        assert "StreamLake" in str(caught.value) and "upstream_provider_shared_pool" in str(caught.value)
        assert "secret-provider-token" not in str(caught.value) + caplog.text
        assert "[redacted]" in str(caught.value)
    finally:
        await policy.close()


async def test_server_delay_outside_budget_never_retries_early(waits):
    policy = Probe(MODEL, mocked(lambda r: httpx.Response(429, headers={"Retry-After": "3600"})))
    try:
        with pytest.raises(InfrastructureFailure, match="Retry-After: 3600s") as caught:
            await decide(policy, {})
        assert not caught.value.retryable
        waits.assert_not_awaited()
    finally:
        await policy.close()


async def test_permanent_quota_is_not_retried(waits):
    policy = Probe(MODEL, mocked(lambda r: httpx.Response(429, json={"error": {
        "type": "insufficient_quota", "message": "Add credits",
    }})))
    try:
        with pytest.raises(InfrastructureFailure, match="insufficient_quota") as caught:
            await decide(policy, {})
        assert not caught.value.retryable
        waits.assert_not_awaited()
    finally:
        await policy.close()


async def test_pause_cancels_backoff_without_another_request(waits):
    waits.side_effect = asyncio.CancelledError()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429)

    policy = Probe(MODEL, mocked(handler))
    try:
        with pytest.raises(asyncio.CancelledError):
            await policy.decide({})
        assert len(requests) == 1
    finally:
        await policy.close()


async def test_health_reports_shared_pool_without_retrying(waits, monkeypatch):
    policy = Probe(MODEL, mocked(lambda r: httpx.Response(429, json={"error": {
        "message": "Provider returned error", "metadata": {
            "provider_name": "StreamLake", "limit_source": "upstream_provider_shared_pool",
        },
    }})))
    monkeypatch.setattr("unimatrix.models.health.ModelClient", lambda cfg: policy)
    result = await check_model(MODEL)
    assert result["code"] == "rate_limit" and result["http_status"] == 429
    assert "shared capacity" in result["hint"] and "StreamLake" in result["provider_detail"]
    waits.assert_not_awaited()


@pytest.mark.parametrize("value", [None, "invalid", "nan", "inf"])
def test_invalid_retry_after_uses_fallback(value):
    assert transport.retry_after_seconds(value) is None


def test_retry_after_supports_http_date_and_seconds():
    future = datetime.now(timezone.utc) + timedelta(seconds=90)
    assert 88 < transport.retry_after_seconds(format_datetime(future, usegmt=True)) <= 90
    assert transport.retry_after_seconds("120") == 120
