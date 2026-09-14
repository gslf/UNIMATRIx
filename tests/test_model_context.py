from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from unimatrix.benchmark.runner import InfrastructureFailure
from unimatrix.benchmark.validation import validate_policy
from unimatrix.policies.llm_policy import LLMPolicy

CONFIG = dict(
    model="test",
    snapshot="frozen",
    endpoint="http://remote:5556",
    context_tokens=32768,
    budget_track="accounted_compute",
)


def server(monkeypatch, context=32768, stop_reason="eosFound", generated=9000):
    stats = SimpleNamespace(
        predicted_tokens_count=generated,
        prompt_tokens_count=2000,
        stop_reason=stop_reason,
        to_dict=lambda: {"stopReason": stop_reason},
    )

    async def respond(prompt, *, response_format, config, on_prediction_fragment):
        assert prompt == '{"observation":"test"}'
        assert response_format == {"type": "object"}
        assert config["contextOverflowPolicy"] == "stopAtLimit"
        assert config["maxTokens"] == CONFIG["context_tokens"]
        on_prediction_fragment(
            SimpleNamespace(reasoning_type="reasoning", content="thinking", tokens_count=8998)
        )
        on_prediction_fragment(SimpleNamespace(reasoning_type="none", content="{}", tokens_count=2))
        return SimpleNamespace(content="thinking{}", stats=stats)

    model = SimpleNamespace(
        identifier="actual-instance",
        get_context_length=AsyncMock(return_value=context),
        respond=AsyncMock(side_effect=respond),
    )
    client = SimpleNamespace(llm=SimpleNamespace(model=AsyncMock(return_value=model)))
    scope = AsyncMock()
    scope.__aenter__.return_value = client
    factory = Mock(return_value=scope)
    monkeypatch.setattr("unimatrix.policies.llm_policy.lmstudio.AsyncClient", factory)
    return factory, scope, client, model


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_reason", ["eosFound", "contextLengthReached"])
async def test_context_controls_generation_and_preserves_long_reasoning(monkeypatch, stop_reason):
    factory, scope, client, model = server(monkeypatch, stop_reason=stop_reason)
    monkeypatch.setenv("MODEL_KEY", "token")
    policy = LLMPolicy(dict(CONFIG, api_key_env="MODEL_KEY"))
    try:
        raw, usage = await policy.decide({"observation": "test"}, {"generation_tokens": 4096})
        assert raw == "{}"
        assert usage["generated_tokens"] == 9000
        assert usage["reasoning_tokens"] == 8998
        assert usage["reasoning"] == "thinking"
        assert usage["finish_reason"] == stop_reason
        assert usage["loaded_context_tokens"] == 32768
        assert usage["model_instance"] == "actual-instance"
        factory.assert_called_once_with(api_host="remote:5556", api_token="token")
        client.llm.model.assert_awaited_once_with("test", config={"contextLength": 32768})
        scope.__aexit__.assert_awaited_once()
    finally:
        await policy.close()


@pytest.mark.asyncio
async def test_context_mismatch_does_not_generate_or_reload(monkeypatch):
    factory, scope, client, model = server(monkeypatch, context=4096)
    policy = LLMPolicy(CONFIG)
    try:
        with pytest.raises(ValueError, match="requested 32768 tokens, loaded 4096"):
            await policy.decide({}, {})
        model.respond.assert_not_awaited()
        scope.__aexit__.assert_awaited_once()
    finally:
        await policy.close()


@pytest.mark.asyncio
async def test_sdk_failure_redacts_credentials_and_cleans_up(monkeypatch):
    factory, scope, client, model = server(monkeypatch)
    model.respond.side_effect = RuntimeError("bad request secret-value")
    monkeypatch.setenv("MODEL_KEY", "secret-value")
    policy = LLMPolicy(dict(CONFIG, api_key_env="MODEL_KEY"))
    try:
        with pytest.raises(InfrastructureFailure, match=r"bad request \[redacted\]"):
            await policy.decide({}, {})
        scope.__aexit__.assert_awaited_once()
    finally:
        await policy.close()


@pytest.mark.parametrize("context", [None, 0, -1, True, 1.5, "32768"])
def test_invalid_context(context):
    with pytest.raises(ValueError, match="context_tokens"):
        validate_policy(dict(CONFIG, context_tokens=context))


def test_native_context_cannot_silently_downgrade_https():
    with pytest.raises(ValueError, match="http_host"):
        validate_policy(dict(CONFIG, endpoint="https://remote"))


@pytest.mark.asyncio
async def test_cancelled_prediction_closes_sdk_scope(monkeypatch):
    import asyncio

    factory, scope, client, model = server(monkeypatch)
    model.respond.side_effect = asyncio.CancelledError()
    policy = LLMPolicy(CONFIG)
    try:
        with pytest.raises(asyncio.CancelledError):
            await policy.decide({}, {})
        scope.__aexit__.assert_awaited_once()
    finally:
        await policy.close()


def test_sdk_serializes_context_policy_without_hidden_output_budget():
    from lmstudio._kv_config import prediction_config_to_kv_config_stack

    structured, stack = prediction_config_to_kv_config_stack(
        {"type": "object"},
        {"maxTokens": 32768, "contextOverflowPolicy": "stopAtLimit", "temperature": 0},
    )
    fields = {item.key: item.value for item in stack.layers[0].config.fields}
    assert structured
    assert fields["llm.prediction.contextOverflowPolicy"] == "stopAtLimit"
    assert fields["llm.prediction.maxPredictedTokens"] == {"checked": True, "value": 32768}
    assert fields["llm.prediction.structured"]["jsonSchema"] == {"type": "object"}


@pytest.mark.asyncio
async def test_model_editor_saves_token_context(tmp_path):
    import httpx

    from unimatrix.web.server import build_app

    app = build_app(tmp_path / "runs", tmp_path / "models", tmp_path / "recipes")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put("/api/models/test", json={"config": CONFIG})
        assert response.status_code == 200
        assert response.json()["context_tokens"] == 32768
        assert "context_bytes_verified" not in response.json()
