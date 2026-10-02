import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI

from unimatrix.actions.schemas import empty
from unimatrix.actions.schemas import validate as validate_decision
from unimatrix.benchmark.recipes import RecipeRepository, bind_candidate
from unimatrix.benchmark.service import BenchmarkService
from unimatrix.core.ids import canonical
from unimatrix.core.visibility import observe
from unimatrix.persistence.event_store import EventStore
from unimatrix.persistence.json_files import read_json, write_json
from unimatrix.policies.llm_policy import LLMPolicy
from unimatrix.research.campaigns import CampaignRunner, analyze, prepare
from unimatrix.research.design import Design, build_design
from unimatrix.research.interventions import IntervenedPolicy
from unimatrix.scenarios import get_scenario
from unimatrix.web.research import build_research_router

MODEL = dict(
    model="test-model",
    snapshot="v1",
    endpoint="http://unused",
    context_bytes_verified=24000,
    budget_track="accounted_compute",
)


def design(**changes):
    return dict(
        base_recipe="standard-v1",
        id="lab-v1",
        name="Lab v1",
        description="Test design",
        domains=["D1"],
        seeds=[100],
        holdout_seeds=[200],
        roles=["advantaged"],
        replicates=1,
        **changes,
    )


def small_plan():
    spec = build_design(Design(**design()), RecipeRepository())["plan"]
    spec["bootstrap"] = dict(seed=1, resamples=10)
    return spec


def test_design_is_balanced_disjoint_and_keeps_base_unchanged():
    plans = RecipeRepository()
    original = plans.get("standard-v1")
    data = design()
    data.update(
        domains=["D1", "D2"],
        roles=["advantaged", "disadvantaged"],
        seeds=[11, 12],
        holdout_seeds=[21, 22, 23],
        replicates=2,
        weights={"D1": {"D1.prediction": 0.2, "D1.decision": 0.4, "D1.update": 0.4}},
    )
    result = build_design(Design(**data), plans)
    assert result["episodes"] == 16
    assert result["holdout_episodes"] == 24
    assert result["plan"]["domains"]["D1"]["metrics"] == data["weights"]["D1"]
    assert {c["seed"] for c in result["holdout"]["cases"]} == {21, 22, 23}
    for key in ["engine", "budgets", "bootstrap", "peers", "ticks"]:
        assert result["plan"][key] == result["holdout"][key] == original[key]
    assert result["holdout"]["id"] == "lab-v1-holdout"
    assert plans.get("standard-v1") == original


@pytest.mark.parametrize(
    "changes",
    [
        {"seeds": [1, 1]},
        {"holdout_seeds": [100]},
        {"domains": []},
        {"complexity": ["extreme"]},
        {"replicates": 0},
        {"seeds": [True]},
        {"peers": []},
        {"id": "../overwrite"},
        {"weights": {"D1": {"D1.prediction": 1, "D1.decision": 0.4, "D1.update": 0.4}}},
        {"peers": ["passive"] * 128},
    ],
)
def test_invalid_designs_are_rejected(changes):
    data = design()
    data.update(changes)
    with pytest.raises(ValueError):
        build_design(Design(**data), RecipeRepository())


def test_preview_pairs_exact_cases_and_counts_reference_model_calls():
    spec = small_plan()
    spec["peers"] = [dict(MODEL, model="peer")] * 2 + ["passive"] * 5
    record = prepare(spec, {"a": MODEL}, ["passive"], ["no_memory", "no_communication"], "Pilot")
    assert len(record["studies"]) == record["episodes"] == 4
    assert record["provider_decisions_max"] == (2 + 3 * 3) * spec["ticks"]
    assert record["provider_attempts_max"] == record["provider_decisions_max"] * 3
    assert record["generated_tokens_max"] is None
    ids = set()
    for study in record["studies"]:
        m = study["execution"]["episodes"][0]
        ids.add(m["run_id"])
        assert "ablations" not in m
        assert study["execution"]["suite"]["cases"] == spec["cases"]
        from unimatrix.benchmark.manifests import peer_slots

    assert [m["policies"][s] for s in peer_slots(m)] == spec["peers"]
    assert len(ids) == 4
    with pytest.raises(ValueError, match="same candidate"):
        prepare(spec, {"a": MODEL, "b": dict(MODEL, endpoint="http://elsewhere")}, [], [], "Pilot")
    with pytest.raises(ValueError, match="20,000"):
        prepare(dict(spec, cases=spec["cases"] * 20001), {}, ["passive"], [], "Pilot")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant", ["no_memory", "no_communication", "rename_agents", "reverse_observation_order"]
)
async def test_interventions_preserve_evidence_and_do_not_repair_invalid_outputs(variant):
    manifest = bind_candidate(small_plan(), MODEL)["episodes"][0]
    scenario = get_scenario("D1")
    packet = observe(scenario.build(manifest), manifest["focal_slot"], scenario)
    packet["private_note"] = "saved note"
    message = dict(sender="peer", content="secret message")
    packet["inbox"] = [message]
    packet["events"] = [dict(type="message_sent", payload=message), dict(type="work")]
    packet["retrieval"] = [message, dict(type="work")]
    packet["objects"]["manual"] = dict(kind="artifact", owner="peer", content="procedure")
    original = deepcopy(packet)

    class Stub:
        fingerprint = "stub"
        invalid = False

        async def decide(self, observation, budget):
            if self.invalid:
                return '{"messages": "invalid"}', {}
            self.seen = deepcopy(observation)
            result = empty(observation["tick"], observation["agent_id"])
            result.update(
                private_note="new note",
                memory_query="recall",
                messages=[dict(channel="public", to=[], content="hello")],
                operations=[dict(verb="publish", kind="other", content="hi", parent_ids=[])],
            )
            return canonical(result), dict(generated_tokens=10)

    stub = Stub()
    policy = IntervenedPolicy(stub, variant, "test-runtime")
    raw, usage = await policy.decide(packet, {})
    result = validate_decision(raw, packet["tick"], packet["agent_id"])
    assert packet == original
    assert usage["research_input"] == stub.seen
    assert json.loads(usage["research_output"])["private_note"] == "new note"
    if variant == "no_memory":
        assert stub.seen["private_note"] == result["private_note"] == ""
        assert not stub.seen["retrieval"] and not stub.seen["events"]
        assert stub.seen["inbox"] == packet["inbox"]
        assert result["memory_query"] is None
    elif variant == "no_communication":
        assert not stub.seen["inbox"] and "manual" not in stub.seen["objects"]
        assert stub.seen["events"] == stub.seen["retrieval"] == [dict(type="work")]
        assert result["messages"] == result["operations"] == []
    elif variant == "rename_agents":
        assert stub.seen["agent_id"] != packet["agent_id"]
        assert all(p["id"].startswith("person-") for p in stub.seen["peers"])
    else:
        assert stub.seen["peers"] == list(reversed(packet["peers"]))
    stub.invalid = True
    raw, _ = await policy.decide(packet, {})
    assert raw == '{"messages": "invalid"}'


@pytest.mark.asyncio
async def test_api_draft_preview_export_and_immutable_publication(tmp_path):
    write_json(tmp_path / "models" / "a.json", MODEL)
    router = build_research_router(
        tmp_path / "runs", tmp_path / "models", tmp_path / "plans", "standard-v1"
    )
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        options = (await client.get("/api/recipe-lab/options")).json()
        assert "prudent" in options["peer_policies"]
        response = await client.post("/api/recipe-lab/drafts", json=design())
        assert response.status_code == 200, response.text
        draft = response.json()
        exported = await client.get(f"/api/recipe-lab/drafts/{draft['id']}/export?split=holdout")
        assert exported.json() == draft["holdout"]
        request = dict(
            draft_id=draft["id"],
            name="Frozen preview",
            model_ids=["a"],
            baselines=[],
            interventions=["no_memory"],
        )
        preview = (await client.post("/api/recipe-lab/evaluation-preview", json=request)).json()
        write_json(tmp_path / "models" / "a.json", dict(MODEL, model="changed"))
        saved = read_json(
            tmp_path / "runs" / "research" / "previews" / preview["id"] / "preview.json"
        )
        m = saved["studies"][0]["execution"]["episodes"][0]
        assert m["policies"][m["focal_slot"]] == MODEL
        assert (await client.get("/api/recipe-lab/evaluations")).json() == []
        assert not list((tmp_path / "runs").rglob("episode.db"))
        publish = f"/api/recipe-lab/drafts/{draft['id']}/publish"
        assert (await client.post(publish, json={})).json()["already_saved"] is False
        assert (await client.post(publish, json={})).json()["already_saved"] is True
        assert RecipeRepository(tmp_path / "plans", "standard-v1").default == "standard-v1"
        changed = design()
        changed["seeds"] = [101]
        other = (await client.post("/api/recipe-lab/drafts", json=changed)).json()
        assert (
            await client.post(f"/api/recipe-lab/drafts/{other['id']}/publish", json={})
        ).status_code == 409
        assert read_json(tmp_path / "plans" / "lab-v1.json") == draft["recipe"]


@pytest.mark.asyncio
async def test_campaign_executes_real_engine_records_candidate_only_variants_and_findings(
    tmp_path, monkeypatch
):
    async def decide(self, observation, budget):
        output = empty(observation["tick"], observation["agent_id"])
        output["private_note"] = "persistent note"
        return canonical(output), dict(generated_tokens=5, purpose="decision")

    monkeypatch.setattr(LLMPolicy, "decide", decide)
    record = prepare(small_plan(), {"a": MODEL}, ["passive"], ["no_memory"], "Engine test")
    record["id"] = "a" * 24
    runner = CampaignRunner(tmp_path / "research" / "campaigns", tmp_path / "benchmark.lock")
    runner.launch(record)
    await runner.active[record["id"]][0]
    assert record["status"] == "completed", record
    assert record["analysis"]["completed_studies"] == 3
    assert len(record["analysis"]["effects"]) == 1
    assert record["analysis"]["effects"][0]["delta"] == 0
    assert all(m["observations"] == 2 for m in record["analysis"]["metric_health"])
    assert record["analysis"]["score_spread"] is None
    assert not list(tmp_path.glob("*/benchmark.json"))
    study = record["studies"][-1]
    manifest = study["execution"]["episodes"][0]
    path = (
        runner.directory
        / record["id"]
        / "studies"
        / study["id"]
        / "episodes"
        / manifest["run_id"]
        / "episode.db"
    )
    store = EventStore(path)
    try:
        assert store.verify()["completed_tick"] == manifest["ticks"]
        rows = store.db.execute("SELECT body FROM model_calls").fetchall()
        calls = [json.loads(row[0]) for row in rows]
        modified = [c for c in calls if "research_intervention" in c]
        assert len(modified) == manifest["ticks"]
        assert all(c["slot"] == manifest["focal_slot"] for c in modified)
        assert all(c["research_input"]["private_note"] == "" for c in modified)
        assert all(
            json.loads(c["research_output"])["private_note"] == "persistent note" for c in modified
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_pause_restart_shared_lock_and_changed_implementation(tmp_path, monkeypatch):
    entered = asyncio.Event()
    waiting = asyncio.Event()

    async def blocked(*args):
        entered.set()
        await waiting.wait()

    monkeypatch.setattr("unimatrix.research.campaigns.run_research_episode", blocked)
    runner = CampaignRunner(tmp_path / "research" / "campaigns", tmp_path / "benchmark.lock")
    record = prepare(small_plan(), {}, ["passive"], [], "Pause test")
    record["id"] = "b" * 24
    runner.launch(record)
    await entered.wait()
    service = BenchmarkService(tmp_path, RecipeRepository())
    with pytest.raises(ValueError, match="episode_already_running"):
        await service.start(MODEL, "a")
    await runner.pause(record)
    assert record["status"] == "paused" and not runner.active
    restored = CampaignRunner(runner.directory, runner.shared_lock)
    saved = read_json(runner.directory / record["id"] / "campaign.json")
    restored.launch(saved)

    await restored.pause(saved)
    assert not restored.active
    saved["research_runtime"] = "changed"
    run = await service.start(MODEL, "a")
    await service.pause(run["id"])
    with pytest.raises(ValueError, match="changed"):
        restored.launch(saved)


def test_analysis_uses_paired_cases_and_excludes_failed_and_intervened_metric_health(tmp_path):
    record = prepare(
        small_plan(), {"a": MODEL, "b": dict(MODEL, model="second")}, [], ["no_memory"], "Analysis"
    )
    for study, score in zip(record["studies"], [1, 0.5, 0, None]):
        if score is None:
            study["status"] = "failed"
            continue
        suite = study["execution"]["suite"]
        from unimatrix.evaluation.scoring import canonical_hash, summarize

        data = dict(
            benchmark_id=suite["benchmark_id"],
            scaffold_id=suite["scaffold_id"],
            suite_hash=canonical_hash(suite),
            candidate_id=study["execution"]["candidate_id"],
            data_kind="synthetic",
            budget_track="accounted_compute",
            runs=[
                dict(
                    c,
                    status="completed",
                    population="P0",
                    scores={m: score for m in suite["domains"][c["domain"]]["metrics"]},
                )
                for c in suite["cases"]
            ],
        )
        write_json(tmp_path / "studies" / study["id"] / "results.json", data)
        study.update(status="completed", report=summarize(data, suite))
    result = analyze(record, tmp_path)
    assert result["completed_studies"] == 3
    assert result["score_spread"] == 100
    assert result["effects"][0]["delta"] == 50
    assert result["effects"][0]["ci95"][0] < 0 < result["effects"][0]["ci95"][1]
    assert all(m["observations"] == 2 and m["mean"] == 50 for m in result["metric_health"])
    assert any("1 studies failed" in n for n in result["notes"])


def test_analysis_normalizes_between_floor_and_ceiling_and_compares_pairs(tmp_path):
    from unimatrix.evaluation.scoring import canonical_hash, summarize

    record = prepare(
        small_plan(),
        {"a": MODEL, "b": dict(MODEL, model="second")},
        ["random", "oracle"],
        [],
        "Normalization",
    )
    levels = {"baseline/random": 0.2, "baseline/oracle": 0.8, "a": 0.5, "b": 0.65}
    for study in record["studies"]:
        suite = study["execution"]["suite"]
        base = levels[study["system_id"]]
        data = dict(
            benchmark_id=suite["benchmark_id"],
            scaffold_id=suite["scaffold_id"],
            suite_hash=canonical_hash(suite),
            candidate_id=study["execution"]["candidate_id"],
            data_kind="synthetic",
            budget_track="accounted_compute",
            runs=[
                dict(
                    c,
                    status="completed",
                    population="P0",
                    scores={
                        m: min(1, base + 0.05 * (c["seed"] % 2))
                        for m in suite["domains"][c["domain"]]["metrics"]
                    },
                )
                for c in suite["cases"]
            ],
        )
        write_json(tmp_path / "studies" / study["id"] / "results.json", data)
        study.update(status="completed", report=summarize(data, suite))
    result = analyze(record, tmp_path)
    assert result["normalized"] is True
    systems = {s["system_id"]: s for s in result["systems"]}
    assert systems["baseline/random"]["rating_points"] == 0
    assert systems["baseline/oracle"]["rating_points"] == 100
    assert 0 < systems["a"]["rating_points"] < systems["b"]["rating_points"] < 100
    assert systems["a"]["iqm"] == pytest.approx(systems["a"]["usi"], abs=5)
    pairs = {(c["left"], c["right"]) for c in result["comparisons"]}
    assert ("test-model", "second") in pairs and ("second", "test-model") not in pairs
    assert all(c["seed_rho"] is None or -1 <= c["seed_rho"] <= 1 for c in result["comparisons"])
    assert result["discrimination"] and all(c["degenerate"] is False for c in result["discrimination"])
    assert all(c["model_spread"] == pytest.approx(15) for c in result["discrimination"])


@pytest.mark.asyncio
async def test_pilot_stops_early_on_paired_seeds(tmp_path, monkeypatch):
    from unimatrix.persistence.json_files import read_json as load

    async def decide(self, observation, budget):
        return canonical(empty(observation["tick"], observation["agent_id"])), {}

    monkeypatch.setattr(LLMPolicy, "decide", decide)
    spec = small_plan()
    spec["cases"] = [dict(c, seed=seed) for seed in [1, 2, 3, 4] for c in spec["cases"][:1]]
    record = prepare(
        spec,
        {"a": MODEL},
        ["passive"],
        [],
        "Pilot",
        mode="pilot",
        stopping=dict(reference="reciprocal", minimum_effect=5, block_seeds=2, max_seeds=4),
    )
    assert [s["system_id"] for s in record["studies"]][:2] == ["baseline/passive", "baseline/reciprocal"]
    record["id"] = "c" * 24
    runner = CampaignRunner(tmp_path / "research" / "campaigns", tmp_path / "benchmark.lock")
    runner.launch(record)
    await runner.active[record["id"]][0]
    assert record["status"] == "completed", record
    study = record["studies"][-1]
    assert study["stopping"]["verdict"] is None
    assert study["stopping"]["seeds"] == [1, 2, 3, 4] and study["stopping"]["looks"] == 2
    assert len(study["execution"]["episodes"]) == 4
    assert study["stopping"]["planned_looks"] == 2
    assert study["stopping"]["family_size"] == 2
    assert study["stopping"]["interval_method"] == "weighted-Hoeffding-seed-clusters-v1"
    saved = load(runner.directory / record["id"] / "campaign.json")
    assert saved["mode"] == "pilot" and saved["studies"][-1]["stopping"]["seeds"] == [1, 2, 3, 4]
    comparisons = record["analysis"]["comparisons"]
    assert any(c["right"] == "reciprocal" and c["verdict"] == "inconclusive" for c in comparisons)


def test_smoke_mode_keeps_one_case_per_domain_and_complexity():
    spec = RecipeRepository().get("standard-v1")
    record = prepare(spec, {}, ["passive"], [], "Smoke", mode="smoke")
    cases = record["plan"]["cases"]
    assert len(cases) == 16
    assert cases == spec["cases"]
    with pytest.raises(ValueError):
        prepare(spec, {}, ["passive"], [], "Bad", mode="pilot", stopping={"reference": "nobody"})
