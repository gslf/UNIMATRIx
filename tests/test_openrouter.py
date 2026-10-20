"""OpenRouter routing, reasoning and credential round trips without paid requests."""

import json
from copy import deepcopy

import httpx
import pytest

from unimatrix.models.config import validate_model
from unimatrix.models.health import check_model
from unimatrix.web.server import build_app

from .test_models import CONTEXT, MODEL, Probe, client_for, mocked, reply


def config_for(provider="deepinfra/fp8", effort="high", temperature=0):
    config = dict(MODEL, endpoint="https://openrouter.ai/api", temperature=temperature,
                  constrain_output=False, max_output_tokens=65536,
                  openrouter={
                      "provider": {"only": [provider], "allow_fallbacks": False,
                                   "require_parameters": True},
                      "reasoning": {"enabled": True, "exclude": False},
                  })
    if provider.endswith("/fp8"):
        config["openrouter"]["provider"]["quantizations"] = ["fp8"]
    if effort:
        config["openrouter"]["reasoning"]["effort"] = effort
    return config


@pytest.mark.parametrize("provider,effort,temperature", [
    ("meta", "medium", 0), ("streamlake/fp8", "max", 0),
    ("deepinfra/fp8", "high", 0), ("anthropic", "medium", None),
    ("gmicloud/fp8", None, 0),
])
async def test_pinned_requests_preserve_reasoning_and_usage(provider, effort, temperature):
    config = config_for(provider, effort, temperature)
    original = deepcopy(config)

    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert body["provider"] == config["openrouter"]["provider"]
        assert body["reasoning"] == config["openrouter"]["reasoning"]
        assert body["model"] == config["model"]
        assert body["max_tokens"] == 65536
        assert "response_format" not in body
        if temperature is None:
            assert "temperature" not in body
        else:
            assert body["temperature"] == temperature
        return httpx.Response(200, json={
            "id": "gen-test", "provider": "Selected provider", "model": "test",
            "choices": [{"message": {"content": "{}", "reasoning": "Think carefully."},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 202,
                      "completion_tokens_details": {"reasoning_tokens": 200}, "cost": 0.0001},
        })

    policy = Probe(config, mocked(handler))
    try:
        answer, usage = await policy.decide({})
        assert answer == "{}"
        assert usage["reasoning"] == "Think carefully."
        assert usage["generated_tokens"] == 202  # Reasoning is already included.
        assert usage["reasoning_tokens"] == 200
        assert usage["provider_usage"]["cost"] == 0.0001
        assert usage["provider_name"] == "Selected provider"
        assert usage["provider_request_id"] == "gen-test"
        assert usage["provider_routing_requested"] == config["openrouter"]["provider"]
        assert usage["reasoning_requested"] == config["openrouter"]["reasoning"]
        assert config == original
    finally:
        await policy.close()


async def test_unavailable_pin_does_not_retry_without_restrictions():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(404, json={"error": {"message": "No endpoints found"}})

    config = config_for()
    policy = Probe(config, mocked(handler))
    try:
        with pytest.raises(httpx.HTTPStatusError, match="No endpoints found"):
            await policy.decide({})
        assert len(calls) == 1
        assert calls[0]["provider"] == config["openrouter"]["provider"]
    finally:
        await policy.close()


@pytest.mark.parametrize("options", [
    None, {}, {"model": "override"}, {"messages": []},
    {"provider": {}}, {"provider": {"only": "anthropic"}},
    {"provider": {"only": []}}, {"provider": {"only": [False]}},
    {"provider": {"allow_fallbacks": "false"}},
    {"provider": {"require_parameters": 1}},
    {"provider": {"quantizations": ["iq3_s"]}},
    {"provider": {"unknown": True}}, {"reasoning": {}},
    {"reasoning": {"enabled": "true"}}, {"reasoning": {"exclude": 0}},
    {"reasoning": {"effort": "maximum"}},
    {"reasoning": {"enabled": True, "effort": "none"}},
    {"reasoning": {"enabled": False, "effort": "high"}},
])
def test_reject_invalid_routing_and_arbitrary_body_overrides(options):
    with pytest.raises(ValueError):
        validate_model(dict(config_for(), openrouter=options))


@pytest.mark.parametrize("endpoint", [
    "http://openrouter.ai/api", "https://other.example/api",
    "https://openrouter.ai/api/v1", "https://openrouter.ai",
])
def test_openrouter_settings_require_correct_endpoint(endpoint):
    with pytest.raises(ValueError, match="openrouter_requires_https_api_endpoint"):
        validate_model(dict(config_for(), endpoint=endpoint))


def test_temperature_omission_preserves_local_default_and_context_validation():
    validate_model(dict(MODEL, temperature=None))
    with pytest.raises(ValueError, match="invalid_temperature"):
        validate_model(dict(CONTEXT, temperature=None))


async def test_old_config_still_sends_zero_temperature():
    def handler(request):
        assert json.loads(request.content)["temperature"] == 0
        return reply(completion_tokens=2)

    policy = Probe(MODEL, mocked(handler))
    try:
        await policy.decide({})
    finally:
        await policy.close()


async def test_dashboard_key_save_preserves_pin_reasoning_and_omitted_temperature(tmp_path):
    models = tmp_path / "models"
    app = build_app(tmp_path / "runs", models, tmp_path / "societies")
    config = config_for("anthropic", "medium", None)
    async with client_for(app) as client:
        client.headers["X-Unimatrix-Request"] = "1"
        response = await client.put("/api/models/haiku", json={
            "config": config, "api_key": "fake-openrouter-test-key",
        })
        assert response.status_code == 200
        saved = response.json()
        assert saved["openrouter"] == config["openrouter"]
        assert saved["temperature"] is None
        assert "fake-openrouter-test-key" not in response.text
        assert "fake-openrouter-test-key" not in (models / "haiku.json").read_text()
        response = await client.put("/api/models/haiku", json={"config": saved})
        assert response.status_code == 200 and response.json() == saved

        def handler(request):
            assert request.headers["Authorization"] == "Bearer fake-openrouter-test-key"
            body = json.loads(request.content)
            assert "temperature" not in body
            assert body["provider"] == config["openrouter"]["provider"]
            assert body["reasoning"]["enabled"] is True
            return reply(completion_tokens=2)

        policy = Probe(saved, mocked(handler))
        try:
            await policy.decide({})
        finally:
            await policy.close()


@pytest.mark.parametrize("cap,expected", [(65536, 8192), (1024, 1024)])
@pytest.mark.parametrize("finish", ["stop", "length"])
async def test_reasoning_health_check_has_bounded_budget_and_detects_truncation(
    monkeypatch, cap, expected, finish,
):
    config = dict(config_for(), max_output_tokens=cap)

    def handler(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == expected
        assert body["reasoning"]["enabled"] is True
        assert body["provider"] == config["openrouter"]["provider"]
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok":true}', "reasoning": "Think."},
                         "finish_reason": finish}],
            "usage": {"completion_tokens": 202},
        })

    policy = Probe(config, mocked(handler))
    monkeypatch.setattr("unimatrix.models.health.ModelClient", lambda cfg: policy)
    result = await check_model(config)
    assert result["ok"] is (finish == "stop")
    assert result["code"] == ("ready" if finish == "stop" else "output_limit")
    assert policy.client.is_closed
