import json
import sqlite3
from contextlib import closing

import httpx
import pytest
from fastapi import FastAPI

from unimatrix.actions.schemas import empty
from unimatrix.benchmark.plans import PlanRepository
from unimatrix.benchmark.runner import Runner
from unimatrix.benchmark.scheduler import bind
from unimatrix.core.ids import canonical
from unimatrix.persistence.event_store import EventStore
from unimatrix.persistence.json_files import write_json
from unimatrix.policies.llm_policy import LLMPolicy
from unimatrix.research.campaigns import prepare, research_fingerprint
from unimatrix.research.interventions import IntervenedPolicy
from unimatrix.scenarios import get_scenario
from unimatrix.web.research import build_research_router

MODEL = dict(
    model="test-model",
    snapshot="frozen",
    endpoint="http://unused",
    context_bytes_verified=24000,
    budget_track="opaque_compute",
)


def small_plan():
    spec = PlanRepository().get("compact-v1")
    spec.update(cases=spec["cases"][:1], domains={"D1": spec["domains"]["D1"]})
    return spec


async def fixture(tmp_path, monkeypatch):
    async def decide(self, observation, budget):
        result = empty(observation["tick"], observation["agent_id"])
        result["messages"] = [dict(channel="public", to=[], content="Recorded explorer message")]
        return canonical(result), dict(generated_tokens=12, input_tokens=24)

    monkeypatch.setattr(LLMPolicy, "decide", decide)
    model = dict(
        MODEL,
        api_key_env="PRIVATE_KEY_LOCATION",
        endpoint="http://localhost:1234",
    )
    spec = small_plan()
    # Two episodes allow testing queued evidence and pagination.
    spec["cases"].append(dict(spec["cases"][0], seed=101))
    record = prepare(spec, {"candidate": model}, ["passive"], ["no_communication"], "Explorer test")
    record.update(id="a" * 24, split="development", status="paused")
    root = tmp_path / "runs"
    folder = root / "research/campaigns" / record["id"]
    for study in record["studies"]:
        manifest = study["execution"]["episodes"][0]
        scenario = get_scenario(manifest["domain"])
        path = folder / "studies" / study["id"] / "episodes" / manifest["run_id"]
        path.mkdir(parents=True)
        store = EventStore(path / "episode.db")
        store.initialize(manifest, scenario.build(manifest))
        router = bind(manifest)
        if study["intervention"]:
            slot = manifest["focal_slot"]
            router.bindings[slot] = IntervenedPolicy(
                router.bindings[slot], study["intervention"], research_fingerprint()
            )
        try:
            await Runner(store, scenario, router).run(until=2)
        finally:
            store.close()
            for policy in router.bindings.values():
                if hasattr(policy, "close"):
                    await policy.close()
        study.update(status="paused", current_episode=manifest["run_id"])
    write_json(folder / "campaign.json", record)
    research = build_research_router(root, tmp_path / "models", tmp_path / "recipes", "standard-v1")
    app = FastAPI()
    app.include_router(research)
    return app, record, research, folder


@pytest.mark.asyncio
async def test_explorer_real_partial_evidence_and_interventions(tmp_path, monkeypatch):
    app, record, _, folder = await fixture(tmp_path, monkeypatch)
    base = "/api/recipe-lab/evaluations/" + record["id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        overview = (await client.get(base + "/explorer")).json()
        assert overview["studies"][0]["candidate"]["kind"] == "scripted"
        assert overview["studies"][1]["candidate"]["endpoint"] == "http://localhost:1234"
        assert "PRIVATE_KEY_LOCATION" not in json.dumps(overview)
        assert "secret" not in json.dumps(overview)
        for study in record["studies"]:
            listing = (await client.get(base + f"/studies/{study['id']}/episodes?limit=1")).json()
            assert listing["total"] == 2 and len(listing["items"]) == 1
            assert listing["items"][0]["completed_tick"] == 2
            manifest = study["execution"]["episodes"][0]
            prefix = base + f"/studies/{study['id']}/episodes/{manifest['run_id']}"
            detail = (await client.get(prefix)).json()
            assert detail["completed_tick"] == 2 and detail["last_decision_tick"] == 1
            assert detail["series"] and detail["status"] == "paused"
            with closing(
                sqlite3.connect(
                    str(
                        folder
                        / "studies"
                        / study["id"]
                        / "episodes"
                        / manifest["run_id"]
                        / "episode.db"
                    )
                )
            ) as db:
                expected_phases = dict(
                    db.execute(
                        "SELECT json_extract(body,'$.phase'),COUNT(*) FROM events "
                        "WHERE tick=2 AND json_extract(body,'$.type') != 'state_committed' "
                        "GROUP BY json_extract(body,'$.phase')"
                    ).fetchall()
                )
            assert {p["phase"]: p["events"] for p in detail["phases"]} == expected_phases
            assert detail["totals"]["provider_attempts"] == (0 if study["id"] == "0" else 2)
            calls = (await client.get(prefix + "/calls?limit=1")).json()
            assert len(calls["items"]) == (0 if study["id"] == "0" else 1)
            if study["id"] != "0":
                assert calls["more"] and calls["items"][0]["tick"] == 0
            state = (await client.get(prefix + "/state?tick=1")).json()
            assert state["tick"] == 1
            assert "PRIVATE_KEY_LOCATION" not in json.dumps(state)
            decision = (
                await client.get(prefix + f"/decision?tick=0&agent={manifest['focal_slot']}")
            ).json()
            assert decision["recorded"] and decision["observation"]["tick"] == 0
            if study["intervention"] == "no_communication":
                call = decision["calls"][0]
                assert call["research_input"]["inbox"] == []
                assert json.loads(call["research_output"])["messages"]
                assert json.loads(decision["response"])["messages"] == []
            events = (await client.get(prefix + "/events?limit=2")).json()
            assert events["more"] and len(events["items"]) == 2
            following = (
                await client.get(prefix + f"/events?after={events['cursor']}&limit=2")
            ).json()
            assert following["items"][0]["seq"] > events["cursor"]
            messages = (await client.get(prefix + "/events?kind=messages")).json()
            if study["id"] == "1":
                assert any(
                    e["payload"]["content"] == "Recorded explorer message"
                    for e in messages["items"]
                )
            pending_id = study["execution"]["episodes"][1]["run_id"]
            pending = (
                await client.get(base + f"/studies/{study['id']}/episodes/{pending_id}")
            ).json()
            assert pending["status"] == "pending" and pending["series"] == []
            assert not (folder / "studies" / study["id"] / "episodes" / pending_id).exists()


@pytest.mark.asyncio
async def test_explorer_scope_missing_decisions_failures_and_live_progress(tmp_path, monkeypatch):
    app, record, research, folder = await fixture(tmp_path, monkeypatch)
    study = record["studies"][1]
    manifest = study["execution"]["episodes"][0]
    base = "/api/recipe-lab/evaluations/" + record["id"]
    prefix = base + f"/studies/1/episodes/{manifest['run_id']}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = (
            await client.get(prefix + f"/decision?tick=2&agent={manifest['focal_slot']}")
        ).json()
        assert missing["recorded"] is False
        assert (await client.get(prefix + "/decision?tick=0&agent=unknown")).status_code == 404
        other_id = record["studies"][0]["execution"]["episodes"][0]["run_id"]
        assert (await client.get(base + f"/studies/1/episodes/{other_id}")).status_code == 404
        assert (await client.get(base + "/studies/999/episodes")).status_code == 404
        assert (await client.get(prefix + "/state?tick=-1")).status_code == 422
        assert (await client.get(prefix + "/events?kind=invalid")).status_code == 422
        assert (await client.get(prefix + "/calls?limit=1000")).status_code == 422
        study.update(status="running", error="Provider error retained")
        record["status"] = "running"
        write_json(folder / "campaign.json", record)
        path = folder / "studies/1/episodes" / manifest["run_id"] / "episode.db"
        with_store = EventStore(path)
        with_store.set_status("running")
        with_store.close()
        assert (await client.get(prefix)).json()["status"] == "interrupted"
        research.runner.active[record["id"]] = None
        try:
            data = (await client.get(base + "/explorer")).json()
            assert data["status"] == "running"
            assert data["studies"][1]["phase"] == "Awaiting decisions"
            assert data["studies"][1]["current"]["saved_decisions"] == 0
            assert data["studies"][1]["error"] == "Provider error retained"
        finally:
            research.runner.active.clear()
        study["status"] = "failed"
        record["status"] = "completed_with_failures"
        write_json(folder / "campaign.json", record)
        data = (await client.get(base + "/explorer")).json()
        assert data["studies"][1]["status"] == "failed" and data["studies"][1]["error"]
