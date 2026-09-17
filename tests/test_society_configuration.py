import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.plans import PlanRepository, bind_candidate
from unimatrix.core.ids import digest
from unimatrix.core.visibility import observe
from unimatrix.policies.llm_policy import LLMPolicy
from unimatrix.research.campaigns import prepare
from unimatrix.research.design import Design, build_design
from unimatrix.scenarios import get_scenario
from unimatrix.web.server import build_app

MODEL = dict(
    model="test-model",
    snapshot="v1",
    endpoint="http://unused",
    context_bytes_verified=24000,
    budget_track="opaque_compute",
)
PERSONALITY = dict(
    personality="Negotiator", system_prompt="Negotiate fair agreements and keep promises."
)


def design(**kwargs):
    return Design(
        id="society",
        name="Society",
        description="Population test",
        domains=["D4"],
        seeds=[100],
        roles=["advantaged"],
        **kwargs,
    )


def test_fixed_rules_and_any_number_of_model_peers_are_frozen():
    peers = [dict(MODEL, **PERSONALITY)] * 3 + ["reciprocal"] * 20
    draft = build_design(design(peers=peers), PlanRepository())
    assert "levels" not in draft["design"]
    assert {case["level"] for case in draft["plan"]["cases"]} == {2}
    prepared = prepare(draft["plan"], {}, ["passive"], [], "Test")
    assert prepared["provider_decisions_max"] == 3 * 240
    manifest = prepared["studies"][0]["execution"]["episodes"][0]
    assert len(manifest["slots"]) == 24
    assert list(p for s, p in manifest["policies"].items() if s != manifest["focal_slot"]) == peers
    peers[0]["system_prompt"] = "Changed after preview"
    assert (
        next(p for p in manifest["policies"].values() if isinstance(p, dict))["system_prompt"]
        == PERSONALITY["system_prompt"]
    )
    changed = build_design(design(peers=peers), PlanRepository())
    assert changed["plan_hash"] != draft["plan_hash"]


@pytest.mark.parametrize("count", [0, 6, 128])
def test_population_limits(count):
    with pytest.raises(ValueError):
        design(peers=["passive"] * count)


@pytest.mark.parametrize("levels", [[1], [3], [1, 2, 3]])
def test_retired_difficulty_is_rejected(levels):
    with pytest.raises(ValueError, match="retired"):
        design(levels=levels)


def test_old_recipe_requires_rebuild_instead_of_silent_rule_changes():
    spec = PlanRepository().get("compact-v1")
    spec["cases"][0]["level"] = 1
    with pytest.raises(ValueError, match="rebuild"):
        bind_candidate(spec, "passive")


@pytest.mark.parametrize("domain", [f"D{i}" for i in range(1, 9)])
def test_large_society_observations_and_feasibility(domain):
    scenario = get_scenario(domain)
    manifest = episode(domain, peer_count=23)
    assert scenario.feasible(manifest)["valid"]
    big = scenario.build(episode(domain, peer_count=127))
    packet = observe(big, "slot-0", scenario)
    assert len(big.agents) == 128
    assert packet["omitted"]["peers"] > 0
    assert len(json.dumps(packet).encode()) < 24000
    first = packet["peers"][0]["id"]
    big.tick += 1
    assert observe(big, "slot-0", scenario)["peers"][0]["id"] != first


def test_resource_stock_and_targets_scale_with_population():
    scenario = get_scenario("D4")
    small = scenario.build(episode("D4"))
    large = scenario.build(episode("D4", peer_count=23))
    assert large.scenario["stock"] == 3 * small.scenario["stock"]
    events = scenario.evolve(large, large)
    sample = next(e for e in events if e["type"] == "service_sampled")
    stock = next(e for e in events if e["type"] == "stock_snapshot")
    assert sample["payload"]["target"] == 125 * 24
    assert stock["payload"]["reserve_target"] == 5000 * 24


@pytest.mark.asyncio
async def test_compatible_provider_receives_system_prompt_without_changing_observation():
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        policy = LLMPolicy(dict(MODEL, **PERSONALITY), client)
        _, usage = await policy.decide({"observation": "test"}, {})
    messages = captured[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert PERSONALITY["system_prompt"] in messages[0]["content"]
    assert "unimatrix.decision.v3" in messages[0]["content"]
    assert json.loads(messages[1]["content"]) == {"observation": "test"}
    assert usage["system_prompt"] == messages[0]["content"]


@pytest.mark.asyncio
async def test_native_provider_receives_personality_system_message(monkeypatch):
    stats = SimpleNamespace(
        predicted_tokens_count=1, prompt_tokens_count=5, stop_reason="eosFound", to_dict=lambda: {}
    )
    model = SimpleNamespace(
        identifier="test",
        get_context_length=AsyncMock(return_value=32768),
        respond=AsyncMock(return_value=SimpleNamespace(stats=stats, content="")),
    )
    scope = AsyncMock()
    scope.__aenter__.return_value = SimpleNamespace(
        llm=SimpleNamespace(model=AsyncMock(return_value=model))
    )
    monkeypatch.setattr("unimatrix.policies.llm_policy.lmstudio.AsyncClient", lambda **kw: scope)
    policy = LLMPolicy(dict(MODEL, context_tokens=32768, **PERSONALITY))
    try:
        await policy.decide({"observation": "test"}, {})
        chat = model.respond.call_args.args[0]
        history = chat._get_history()
        assert PERSONALITY["system_prompt"] in str(history)
        assert history["messages"][0]["role"] == "system"
        assert history["messages"][1]["role"] == "user"
    finally:
        await policy.close()


@pytest.mark.asyncio
async def test_lab_roundtrip_preserves_population_and_distinct_personalities(tmp_path):
    app = build_app(tmp_path / "runs", tmp_path / "models", tmp_path / "recipes")
    peers = [
        dict(MODEL, **PERSONALITY),
        dict(MODEL, personality="Selfish", system_prompt="Maximize your own gain."),
    ] + ["reciprocal"] * 21
    payload = design(peers=peers).model_dump()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/recipe-lab/drafts", json=payload)
        assert response.status_code == 200, response.text
        record = response.json()
        loaded = (await client.get("/api/recipe-lab/drafts/" + record["id"])).json()
        assert loaded["recipe"]["peers"] == peers
        assert len(loaded["recipe"]["cases"]) == 1
        assert "levels" not in loaded["design"]
        assert digest(loaded["recipe"]) == digest(record["recipe"])


def test_direct_runs_cannot_use_retired_difficulty():
    from unimatrix.benchmark.validation import validate_manifest
    from unimatrix.config.models import Config

    with pytest.raises(ValueError):
        Config(mode="core", domain="D1", level=1)
    with pytest.raises(ValueError, match="invalid_episode_shape"):
        validate_manifest(episode("D1", level=1))


def test_larger_free_society_is_supported():
    from unimatrix.benchmark.validation import validate_manifest
    from unimatrix.config.models import Config

    manifest = Config(mode="society", domain="social", population=24, capacity=32).manifest()
    validate_manifest(manifest)
    assert get_scenario("social").feasible(manifest)


@pytest.mark.parametrize(
    "fields",
    [
        {"personality": "Negotiator"},
        {"system_prompt": " "},
        {"system_prompt": "a" * 8001},
        {"system_prompt": 123},
    ],
)
def test_invalid_personality_is_rejected(fields):
    from unimatrix.benchmark.validation import validate_policy

    with pytest.raises(ValueError):
        validate_policy(dict(MODEL, **fields))
