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
        assert '"role": "system"' in str(prompt)
        assert "test" in str(prompt)
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


@pytest.mark.asyncio
async def test_strict_output_keeps_decisions_in_the_model_and_reuses_interface_prefix():
    import json

    import httpx

    from unimatrix.actions.schemas import SCHEMA, empty
    from unimatrix.core.visibility import PROTOCOL

    packet = dict(
        interface={"operations": {"work": {"verb": "work", "project_id": "string"}}},
        tick=7,
        agent_id="slot-2",
        scenario={"available_actions": []},
    )

    async def response(request):
        body = json.loads(request.content)
        assert body["messages"][0] == {"role": "system", "content": PROTOCOL}
        prompt = body["messages"][1]["content"]
        assert prompt.startswith('{"interface":') and json.loads(prompt) == packet
        schema = body["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["tick"] == {"const": 7}
        assert schema["properties"]["agent_id"] == {"const": "slot-2"}
        assert schema["properties"]["operations"] == SCHEMA["properties"]["operations"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(empty(7, "slot-2"))},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"completion_tokens": 40, "prompt_tokens": 100},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    policy = LLMPolicy(
        dict(
            model="micro",
            snapshot="sha",
            endpoint="http://localhost",
            context_bytes_verified=24000,
            budget_track="accounted_compute",
            structured_output="decision_schema",
        ),
        client=client,
    )
    try:
        raw, usage = await policy.decide(packet, {})
        assert json.loads(raw) == empty(7, "slot-2") and usage["generated_tokens"] == 40
        assert "const" not in SCHEMA["properties"]["tick"]
    finally:
        await policy.close()


def test_compact_transport_never_repairs_or_selects_actions():
    import json

    from unimatrix.actions.schemas import validate

    config = dict(
        model="micro",
        snapshot="sha",
        endpoint="http://localhost",
        context_bytes_verified=24000,
        budget_track="accounted_compute",
        structured_output="compact_decision",
    )
    policy = LLMPolicy(config, client=SimpleNamespace())
    packet = dict(tick=3, agent_id="slot-1")
    raw = '{"operations":[{"verb":"work","project_id":"chosen-by-model"}]}'
    decision = validate(policy.expand_response(raw, packet), 3, "slot-1")
    assert decision["operations"] == json.loads(raw)["operations"]
    assert decision["messages"] == [] and decision["private_note"] is None
    for bad in [
        '{"operations":[],"operations":[]}',
        '{"forecasts":[NaN]}',
        '{"repair_me":true}',
        '{"operations":"wrong"}',
    ]:
        expanded = policy.expand_response(bad, packet)
        with pytest.raises(Exception):
            validate(expanded, 3, "slot-1")


async def test_managed_sdk_instance_is_reused_then_only_own_instance_unloaded(monkeypatch):
    factory, scope, client, model = server(monkeypatch)
    client.llm.load_new_instance = AsyncMock(return_value=model)
    client.llm.unload = AsyncMock()
    policy = LLMPolicy(dict(CONFIG, managed_instance=True, max_input_tokens=3000))
    model.apply_prompt_template = AsyncMock(return_value="rendered prompt")
    model.count_tokens = AsyncMock(return_value=2000)
    try:
        await policy.decide({"observation": "test"}, {})
        await policy.decide({"observation": "test"}, {})
        client.llm.load_new_instance.assert_awaited_once_with("test", policy.instance_id, ttl=120, config={"contextLength": 32768})
        client.llm.model.assert_awaited_once_with(policy.instance_id, config={"contextLength": 32768}, ttl=120)
        model.count_tokens.assert_awaited_with("rendered prompt")
    finally:
        await policy.close()
    client.llm.unload.assert_awaited_once_with(policy.instance_id)


async def test_input_token_budget_stops_before_generation(monkeypatch):
    _, _, _, model = server(monkeypatch)
    model.apply_prompt_template = AsyncMock(return_value="rendered prompt")
    model.count_tokens = AsyncMock(return_value=2001)
    policy = LLMPolicy(dict(CONFIG, max_input_tokens=2000))
    try:
        with pytest.raises(ValueError, match="input_token_budget_exceeded"):
            await policy.decide({"observation": "test"}, {})
        model.respond.assert_not_awaited()
    finally:
        await policy.close()
