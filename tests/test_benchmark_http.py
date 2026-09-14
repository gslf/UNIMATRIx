import json

import httpx
import pytest

from unimatrix.benchmark.runner import InfrastructureFailure
from unimatrix.policies.llm_policy import LLMPolicy


@pytest.mark.asyncio
async def test_legacy_adapter_has_no_fixed_generation_cap():
    cfg = dict(
        model="test",
        snapshot="sha256:immutable",
        endpoint="http://unused",
        context_bytes_verified=24000,
        budget_track="accounted_compute",
    )

    def handler(request):
        assert "max_completion_tokens" not in json.loads(request.content)
        assert "max_tokens" not in json.loads(request.content)
        fmt = json.loads(request.content)["response_format"]
        assert fmt["type"] == "json_schema"
        # Do not force a valid decision: malformed envelopes must still be scored.
        assert fmt["json_schema"]["schema"] == {"type": "object"}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": 5000},
            },
        )

    policy = LLMPolicy(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    raw, usage = await policy.decide({}, dict(generation_tokens=4096))
    assert raw == "{}" and usage["generated_tokens"] == 5000
    await policy.close()


@pytest.mark.asyncio
async def test_retryable_provider_error():
    cfg = dict(
        model="test",
        snapshot="frozen",
        endpoint="http://unused",
        context_bytes_verified=24000,
        budget_track="accounted_compute",
    )
    policy = LLMPolicy(
        cfg, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    )
    try:
        with pytest.raises(InfrastructureFailure):
            await policy.decide({}, dict(generation_tokens=4096))
    finally:
        await policy.close()


@pytest.mark.asyncio
async def test_bad_request_preserves_provider_reason_and_redacts_key(monkeypatch):
    monkeypatch.setenv("UNIMATRIX_TEST_KEY", "secret-test-token")
    cfg = dict(
        model="test",
        snapshot="frozen",
        endpoint="http://unused",
        context_bytes_verified=24000,
        budget_track="opaque_compute",
        api_key_env="UNIMATRIX_TEST_KEY",
    )
    policy = LLMPolicy(
        cfg,
        httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(400, json={
                "error": "response_format unsupported; secret-test-token",
            })
        )),
    )
    try:
        with pytest.raises(httpx.HTTPStatusError, match="response_format unsupported") as exc:
            await policy.decide({}, dict(generation_tokens=4096))
        assert "secret-test-token" not in str(exc.value)
        assert "[redacted]" in str(exc.value)
    finally:
        await policy.close()
