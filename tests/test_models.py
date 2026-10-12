"""Candidate models: validation, the inference transport, credentials and dashboard security."""

import asyncio
import json
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from unimatrix.models.config import validate_model
from unimatrix.models.managed import ManagedModel
from unimatrix.models.personas import catalog, validate_persona
from unimatrix.models.transport import InfrastructureFailure, ModelClient
from unimatrix.polis.llm import SCHEMA, StewardPolicy, system_prompt
from unimatrix.polis.recipe import PolisRecipes
from unimatrix.polis.world import World
from unimatrix.web.server import build_app

MODEL = dict(
    model="test",
    snapshot="frozen",
    endpoint="http://localhost:8080",
    context_bytes_verified=24000,
    budget_track="accounted_compute",
)
CONTEXT = dict(
    model="test",
    snapshot="frozen",
    endpoint="http://remote:5556",
    context_tokens=32768,
    budget_track="accounted_compute",
)


class Probe(ModelClient):
    def system_prompt(self):
        return "You are a test steward."


def reply(content="{}", **usage):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                     "usage": usage})


def mocked(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("field,value", [
    ("seed", "3"), ("seed", True), ("temperature", "0"), ("temperature", float("nan")),
    ("max_output_tokens", 0), ("request_timeout_seconds", -1), ("max_input_tokens", False),
    ("output_price_per_million", float("inf")), ("system_prompt", " "), ("system_prompt", "a" * 8001),
    ("system_prompt", 123), ("unknown", 1),
])
def test_strict_model_fields(field, value):
    with pytest.raises(ValueError):
        validate_model(dict(MODEL, **{field: value}))


def test_native_context_cannot_silently_downgrade_https():
    with pytest.raises(ValueError, match="http_host"):
        validate_model(dict(CONTEXT, endpoint="https://remote"))


@pytest.mark.parametrize("broken", [
    {"big_five": {"O": 11, "C": 5, "E": 5, "A": 5, "N": 5}},
    {"orientation": "chaotic"},
    {"conventions": [""]},
])
def test_invalid_personas_are_rejected(broken):
    persona = {k: v for k, v in catalog()["neutral"].items() if k not in {"id", "system_prompt"}}
    validate_persona(persona)
    with pytest.raises(ValueError):
        validate_persona(dict(persona, **broken))


async def test_http_adapter_has_no_fixed_generation_cap():
    def handler(request):
        body = json.loads(request.content)
        assert "max_tokens" not in body and "max_completion_tokens" not in body
        assert body["response_format"]["json_schema"]["schema"] == {"type": "object"}
        assert body["messages"][0] == {"role": "system", "content": "You are a test steward."}
        return reply(completion_tokens=5000)

    policy = Probe(MODEL, mocked(handler))
    try:
        raw, usage = await policy.decide({})
        assert raw == "{}" and usage["generated_tokens"] == 5000
    finally:
        await policy.close()


async def test_retryable_provider_error():
    policy = Probe(MODEL, mocked(lambda r: httpx.Response(429)))
    try:
        with pytest.raises(InfrastructureFailure) as error:
            await policy.decide({})
        assert error.value.retryable
    finally:
        await policy.close()


async def test_bad_request_preserves_provider_reason_and_redacts_key(monkeypatch):
    monkeypatch.setenv("UNIMATRIX_TEST_KEY", "secret-test-token")
    policy = Probe(dict(MODEL, budget_track="opaque_compute", api_key_env="UNIMATRIX_TEST_KEY"),
                   mocked(lambda r: httpx.Response(400, json={"error": "response_format unsupported; secret-test-token"})))
    try:
        with pytest.raises(httpx.HTTPStatusError, match="response_format unsupported") as exc:
            await policy.decide({})
        assert "secret-test-token" not in str(exc.value) and "[redacted]" in str(exc.value)
    finally:
        await policy.close()


async def test_total_deadline_and_cancellation():
    async def blocked(request):
        await asyncio.Event().wait()

    async with mocked(blocked) as client:
        policy = Probe(dict(MODEL, request_timeout_seconds=0.01), client=client)
        with pytest.raises(InfrastructureFailure, match="provider_decision_timeout"):
            await asyncio.wait_for(policy.decide({}), timeout=1)
        policy.request_timeout_seconds = 120
        task = asyncio.create_task(policy.decide({}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_reasoning_delimiters_split_thought_from_answer():
    policy = Probe(dict(MODEL, reasoning_delimiters=["<think>", "</think>"], constrain_output=False),
                   mocked(lambda r: reply("<think>weigh the well</think>\n{\"a\":1}", completion_tokens=9)))
    try:
        raw, usage = await policy.decide({})
        assert raw == '{"a":1}' and usage["reasoning"] == "weigh the well"
    finally:
        await policy.close()


async def test_steward_policy_sends_static_system_prompt_and_decision_schema():
    recipe = PolisRecipes(None).get("standard-v2")
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return reply('{"actions":[]}', completion_tokens=12, prompt_tokens=900)

    policy = StewardPolicy(dict(MODEL, structured_output="decision_schema", system_prompt="Be kind."),
                           recipe, mocked(handler))
    world = World(recipe, 1)
    try:
        await policy.decide(world.observe())
        world.step({"actions": []})
        await policy.decide(world.observe())
    finally:
        await policy.close()
    first, second = seen
    assert first["messages"][0]["content"].startswith(system_prompt(recipe))
    assert first["messages"][0]["content"].endswith("Your personality:\nBe kind.")
    assert first["messages"][0] == second["messages"][0]
    assert first["response_format"]["json_schema"]["schema"] == SCHEMA


def lmstudio_server(monkeypatch, context=32768, stop_reason="eosFound", generated=9000):
    stats = SimpleNamespace(predicted_tokens_count=generated, prompt_tokens_count=2000,
                            stop_reason=stop_reason, to_dict=lambda: {"stopReason": stop_reason})

    async def respond(prompt, *, response_format, config, on_prediction_fragment):
        assert '"role": "system"' in str(prompt) and "test" in str(prompt)
        assert response_format == {"type": "object"}
        assert config["contextOverflowPolicy"] == "stopAtLimit"
        assert config["maxTokens"] == CONTEXT["context_tokens"]
        on_prediction_fragment(SimpleNamespace(reasoning_type="reasoning", content="thinking", tokens_count=8998))
        on_prediction_fragment(SimpleNamespace(reasoning_type="none", content="{}", tokens_count=2))
        return SimpleNamespace(content="thinking{}", stats=stats)

    model = SimpleNamespace(identifier="actual-instance", get_context_length=AsyncMock(return_value=context),
                            respond=AsyncMock(side_effect=respond))
    client = SimpleNamespace(llm=SimpleNamespace(model=AsyncMock(return_value=model)))
    scope = AsyncMock()
    scope.__aenter__.return_value = client
    factory = Mock(return_value=scope)
    monkeypatch.setattr("unimatrix.models.transport.lmstudio.AsyncClient", factory)
    return factory, scope, client, model


@pytest.mark.parametrize("stop_reason", ["eosFound", "contextLengthReached"])
async def test_context_controls_generation_and_preserves_long_reasoning(monkeypatch, stop_reason):
    factory, scope, client, model = lmstudio_server(monkeypatch, stop_reason=stop_reason)
    monkeypatch.setenv("MODEL_KEY", "token")
    policy = Probe(dict(CONTEXT, api_key_env="MODEL_KEY"))
    try:
        raw, usage = await policy.decide({"observation": "test"})
        assert raw == "{}"
        assert usage["generated_tokens"] == 9000 and usage["reasoning_tokens"] == 8998
        assert usage["reasoning"] == "thinking" and usage["finish_reason"] == stop_reason
        assert usage["loaded_context_tokens"] == 32768 and usage["model_instance"] == "actual-instance"
        factory.assert_called_once_with(api_host="remote:5556", api_token="token")
        client.llm.model.assert_awaited_once_with("test", config={"contextLength": 32768})
        scope.__aexit__.assert_awaited_once()
    finally:
        await policy.close()


async def test_context_mismatch_does_not_generate_or_reload(monkeypatch):
    _, scope, _, model = lmstudio_server(monkeypatch, context=4096)
    policy = Probe(CONTEXT)
    try:
        with pytest.raises(ValueError, match="requested 32768 tokens, loaded 4096"):
            await policy.decide({})
        model.respond.assert_not_awaited()
        scope.__aexit__.assert_awaited_once()
    finally:
        await policy.close()


async def test_sdk_failure_redacts_credentials_and_cleans_up(monkeypatch):
    _, scope, _, model = lmstudio_server(monkeypatch)
    model.respond.side_effect = RuntimeError("bad request secret-value")
    monkeypatch.setenv("MODEL_KEY", "secret-value")
    policy = Probe(dict(CONTEXT, api_key_env="MODEL_KEY"))
    try:
        with pytest.raises(InfrastructureFailure, match=r"bad request \[redacted\]"):
            await policy.decide({})
        scope.__aexit__.assert_awaited_once()
    finally:
        await policy.close()


async def test_cancelled_prediction_closes_sdk_scope(monkeypatch):
    _, scope, _, model = lmstudio_server(monkeypatch)
    model.respond.side_effect = asyncio.CancelledError()
    policy = Probe(CONTEXT)
    try:
        with pytest.raises(asyncio.CancelledError):
            await policy.decide({})
        scope.__aexit__.assert_awaited_once()
    finally:
        await policy.close()


async def test_managed_sdk_instance_is_reused_then_only_own_instance_unloaded(monkeypatch):
    _, _, client, model = lmstudio_server(monkeypatch)
    client.llm.load_new_instance = AsyncMock(return_value=model)
    client.llm.unload = AsyncMock()
    policy = Probe(dict(CONTEXT, managed_instance=True, max_input_tokens=3000))
    model.apply_prompt_template = AsyncMock(return_value="rendered prompt")
    model.count_tokens = AsyncMock(return_value=2000)
    try:
        await policy.decide({"observation": "test"})
        await policy.decide({"observation": "test"})
        client.llm.load_new_instance.assert_awaited_once_with(
            "test", policy.instance_id, ttl=120, config={"contextLength": 32768})
        client.llm.model.assert_awaited_once_with(policy.instance_id, config={"contextLength": 32768}, ttl=120)
        model.count_tokens.assert_awaited_with("rendered prompt")
    finally:
        await policy.close()
    client.llm.unload.assert_awaited_once_with(policy.instance_id)


def overlapping(target, name, seconds=0.01):
    """Wrap an async mock so the test can see how many calls were in flight at once."""
    inner, flight = getattr(target, name), dict(now=0, peak=0, calls=0)

    async def tracked(*args, **kwargs):
        flight.update(now=flight["now"] + 1, calls=flight["calls"] + 1)
        flight["peak"] = max(flight["peak"], flight["now"])
        await asyncio.sleep(seconds)
        flight["now"] -= 1
        return await inner(*args, **kwargs)

    setattr(target, name, AsyncMock(side_effect=tracked))
    return flight


async def test_parallel_seeds_load_one_managed_instance_and_predict_concurrently(monkeypatch):
    _, _, client, model = lmstudio_server(monkeypatch)
    client.llm.load_new_instance = AsyncMock(return_value=model)
    client.llm.unload = AsyncMock()
    loads = overlapping(client.llm, "load_new_instance")
    predictions = overlapping(model, "respond")
    config = dict(CONTEXT, managed_instance=True)
    instance = ManagedModel(config)
    seeds = [Probe(config, instance=instance) for _ in range(2)]
    for _ in range(2):
        await asyncio.gather(*(seed.decide({"observation": "test"}) for seed in seeds))
    assert loads["calls"] == 1 and predictions["calls"] == 4 and predictions["peak"] == 2
    for seed in seeds:
        await seed.close()
    client.llm.unload.assert_not_awaited()
    await instance.close()
    client.llm.unload.assert_awaited_once_with(instance.identifier)


async def test_parallel_seeds_never_race_to_load_the_model_key(monkeypatch):
    _, _, client, model = lmstudio_server(monkeypatch)
    lookups = overlapping(client.llm, "model")
    predictions = overlapping(model, "respond", seconds=0.1)  # predictions outlast lookups
    instance = ManagedModel(CONTEXT)
    seeds = [Probe(CONTEXT, instance=instance) for _ in range(3)]
    await asyncio.gather(*(seed.decide({"observation": "test"}) for seed in seeds))
    assert lookups["peak"] == 1 and predictions["peak"] == 3


async def test_first_http_request_runs_alone_then_requests_overlap():
    flight = dict(now=0, starts=[])

    async def handler(request):
        flight["now"] += 1
        flight["starts"].append(flight["now"])
        await asyncio.sleep(0.01)
        flight["now"] -= 1
        return reply(completion_tokens=3)

    instance = ManagedModel(MODEL)
    async with mocked(handler) as http:
        seeds = [Probe(MODEL, http, instance) for _ in range(3)]
        await asyncio.gather(*(seed.decide({}) for seed in seeds))
    assert flight["starts"][0] == 1 and max(flight["starts"]) == 2


async def test_input_token_budget_stops_before_generation(monkeypatch):
    _, _, _, model = lmstudio_server(monkeypatch)
    model.apply_prompt_template = AsyncMock(return_value="rendered prompt")
    model.count_tokens = AsyncMock(return_value=2001)
    policy = Probe(dict(CONTEXT, max_input_tokens=2000))
    try:
        with pytest.raises(ValueError, match="input_token_budget_exceeded"):
            await policy.decide({"observation": "test"})
        model.respond.assert_not_awaited()
    finally:
        await policy.close()


async def test_personality_reaches_the_native_system_message(monkeypatch):
    _, _, _, model = lmstudio_server(monkeypatch)
    recipe = PolisRecipes(None).get("standard-v2")
    model.respond = AsyncMock(return_value=SimpleNamespace(
        content="{}", stats=SimpleNamespace(predicted_tokens_count=1, prompt_tokens_count=5,
                                            stop_reason="eosFound", to_dict=lambda: {})))
    policy = StewardPolicy(dict(CONTEXT, system_prompt="Negotiate fair agreements."), recipe)
    try:
        await policy.decide(World(recipe, 1).observe())
        history = model.respond.call_args.args[0]._get_history()
        assert history["messages"][0]["role"] == "system" and history["messages"][1]["role"] == "user"
        assert "Negotiate fair agreements." in str(history)
    finally:
        await policy.close()


def client_for(app, base="http://localhost"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base)


async def test_model_editor_keeps_credentials_apart(tmp_path):
    models = tmp_path / "models"
    app = build_app(tmp_path / "runs", models, tmp_path / "societies")
    config = dict(MODEL, budget_track="opaque_compute")
    async with client_for(app) as client:
        client.headers["X-Unimatrix-Request"] = "1"
        assert (await client.get("/")).status_code == 200
        response = await client.put("/api/models/test", json={"config": config, "api_key": "test-secret"})
        assert response.status_code == 200
        saved = response.json()
        secret = Path(saved["api_key_file"])
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600
        assert "test-secret" not in response.text
        assert "test-secret" not in (models / "test.json").read_text()
        assert len((await client.get("/api/models")).json()) == 1
        assert (await client.put("/api/models/test", json={"config": saved})).json() == saved
        assert "neutral" in (await client.get("/api/personalities")).json()

        def provider(request):
            assert request.headers["Authorization"] == "Bearer test-secret"
            assert str(request.url) == "http://localhost:8080/v1/chat/completions"
            return reply()

        async with mocked(provider) as transport:
            await Probe(saved, client=transport).decide({})
        response = await client.put("/api/models/test", json={"config": saved, "api_key": "replacement"})
        assert response.json()["api_key_file"] != str(secret)
        assert json.loads(secret.read_text())["api_key"] == "test-secret"
        invalid = dict(config, context_bytes_verified=0)
        assert (await client.put("/api/models/test", json={"config": invalid, "api_key": "x"})).status_code == 422
        response = await client.put("/api/models/test", json={"config": saved, "api_key": ""})
        assert "api_key_file" not in response.json()
        assert (await client.put("/api/models/.hidden", json={"config": config})).status_code == 422
        (models / "link.json").symlink_to(tmp_path / "outside.json")
        assert (await client.put("/api/models/link", json={"config": config})).status_code == 422


async def test_dashboard_security_and_credential_confinement(tmp_path):
    app = build_app(tmp_path / "runs", tmp_path / "models", tmp_path / "societies")
    headers = {"X-Unimatrix-Request": "1"}
    async with client_for(app) as client:
        assert (await client.get("/", headers={"Host": "attacker.example"})).status_code == 400
        assert (await client.get("/api/models", headers={"Origin": "https://attacker.example"})).status_code == 403
        assert (await client.put("/api/models/a", json={"config": MODEL})).status_code == 403
        assert (await client.put("/api/models/a", headers=headers,
                                 json={"config": dict(MODEL, api_key_file="/tmp/secret")})).status_code == 422
        saved = await client.put("/api/models/a", headers=headers, json={"config": MODEL, "api_key": "local-key"})
        assert saved.status_code == 200
        changed = dict(saved.json(), endpoint="https://attacker.example")
        assert (await client.put("/api/models/a", headers=headers, json={"config": changed})).status_code == 422
        assert (await client.put("/api/models/a", headers=dict(headers, Origin="null"),
                                 json={"config": MODEL})).status_code == 403
        assert (await client.get("/")).headers["x-frame-options"] == "DENY"
        for page in ("/recipe-lab", "/society/runs/x"):
            assert (await client.get(page)).status_code == 200
        assert (await client.get("/legacy")).status_code == 404
    token = "t" * 32
    app = build_app(tmp_path / "runs", tmp_path / "models", tmp_path / "societies",
                    auth_token=token, allowed_hosts=["research.internal"])
    async with client_for(app, "http://research.internal") as client:
        assert (await client.get("/api/models")).status_code == 401
        assert (await client.get("/api/models", headers={"Authorization": "Bearer " + token})).status_code == 200
        assert (await client.get("/", auth=("operator", token))).status_code == 200
