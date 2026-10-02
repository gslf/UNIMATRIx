"""Decision probes replay recorded decision points as single calls scored against the oracle."""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from tests.test_research import MODEL, small_plan
from unimatrix.actions.schemas import empty
from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.scheduler import run_episode
from unimatrix.core.ids import canonical
from unimatrix.persistence.json_files import write_json
from unimatrix.policies.llm_policy import LLMPolicy
from unimatrix.research.campaigns import CampaignRunner, prepare
from unimatrix.research.probes import (
    ACQUISITION,
    SCORER,
    ProbeRunner,
    agreement,
    harvest_episode,
    observable_references,
    probe_ticks,
    screen,
)
from unimatrix.web.research import build_research_router


class OracleEcho:
    """Answers every probe with the oracle decision stored in the bank."""

    fingerprint = "echo"

    def __init__(self, bank):
        self.answers = {canonical(p["observation"]): p["oracle"] for p in bank["probes"]}

    async def decide(self, observation, budget):
        return canonical(self.answers[canonical(observation)]), {}


def test_agreement_scores_operations_and_forecasts():
    oracle = dict(
        empty(18, "slot-1"),
        operations=[dict(verb="work", project_id="route-1")],
        forecasts=[dict(probe_id="fact-0", probabilities=[0, 1])],
    )
    perfect = agreement(oracle, oracle)
    assert perfect["score"] == 1 and perfect["operations"] == 1 and perfect["forecasts"] == 1
    wrong = agreement(
        dict(empty(18, "slot-1"), operations=[dict(verb="work", project_id="route-0")]), oracle
    )
    assert wrong["operations"] == 0 and wrong["forecasts"] == 0
    assert agreement(empty(0, "a"), empty(0, "a"))["score"] is None


async def test_harvest_and_screen_against_a_recorded_episode(tmp_path):
    manifest = episode("D1", candidate="reciprocal")
    await run_episode(manifest, tmp_path)
    probes = harvest_episode(tmp_path / manifest["run_id"] / "episode.db", manifest)
    assert [p["tick"] for p in probes] == probe_ticks(manifest)
    assert all(p["oracle"]["forecasts"] for p in probes)
    bank = dict(probes=probes)
    for probe in probes:
        probe["references"] = await observable_references(probe)
    oracle_result = await screen(bank, OracleEcho(bank), parallelism=3)
    assert oracle_result["summary"]["D1"]["oracle_agreement"] == 1
    echo_bank = dict(probes=[dict(p, oracle=p["references"]["reciprocal"]) for p in probes])
    result = await screen(bank, OracleEcho(echo_bank), parallelism=3)
    assert result["agreement"] == 1 and result["summary"]["D1"]["validity"] == 1

    class Broken:
        fingerprint = "broken"

        async def decide(self, observation, budget):
            return "not json", {}

    broken = await screen(bank, Broken(), parallelism=2)
    assert broken["agreement"] == 0 and broken["summary"]["D1"]["validity"] == 0


@pytest.mark.asyncio
async def test_probe_endpoints_harvest_and_screen(tmp_path, monkeypatch):
    async def decide(self, observation, budget):
        return canonical(empty(observation["tick"], observation["agent_id"])), {}

    monkeypatch.setattr(LLMPolicy, "decide", decide)
    record = prepare(small_plan(), {}, ["reciprocal"], [], "Probe source", mode="smoke")
    record["id"] = "d" * 24
    root = tmp_path / "runs"
    runner = CampaignRunner(root / "research" / "campaigns", root / "benchmark.lock")
    runner.launch(record)
    await runner.active[record["id"]][0]
    assert record["status"] == "completed"
    write_json(tmp_path / "models" / "fake.json", MODEL)
    router = build_research_router(root, tmp_path / "models", tmp_path / "recipes", "standard-v1")
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        empty_status = await client.get(f"/api/recipe-lab/evaluations/{record['id']}/probes")
        assert empty_status.status_code == 200 and empty_status.json()["bank"] is None
        harvested = await client.post(f"/api/recipe-lab/evaluations/{record['id']}/probes")
        assert harvested.status_code == 200, harvested.text
        assert harvested.json()["bank"]["by_domain"]["D1"] > 0
        started = await client.post(
            f"/api/recipe-lab/evaluations/{record['id']}/probe-screens",
            json={"model_ids": ["fake"], "parallelism": 2},
        )
        assert started.status_code == 200, started.text
        assert started.json()["running"] == ["fake"]
        for _ in range(200):
            status = (await client.get(f"/api/recipe-lab/evaluations/{record['id']}/probes")).json()
            if status["screens"] and status["screens"][0]["status"] != "running":
                break
            await asyncio.sleep(0.05)
        assert status["screens"][0]["status"] == "completed", status
        assert status["screens"][0]["agreement"] < 1
        assert status["screens"][0]["correlation"] is None
        assert status["screens"][0]["comparison_scope"] == "smoke"
        details = await client.get(f"/api/recipe-lab/evaluations/{record['id']}/probe-screens/fake")
        assert details.status_code == 200
        assert details.json()["rows"][0]["reference_decisions"].keys() == {"reciprocal", "coordinator"}
        assert (await client.get(f"/api/recipe-lab/evaluations/{record['id']}/probe-screens/fake?offset=-1")).status_code == 422
    await router.shutdown_tasks()


def test_content_match_includes_quantities_nested_terms_order_and_duplicates():
    from copy import deepcopy
    a = dict(empty(0, "a"), operations=[
        dict(verb="offer", terms={"legs": [{"quantity_milli": 100, "to_id": "b"}]}),
        dict(verb="transfer", recipient_id="b", resource_id="food", quantity_milli=10),
    ])
    b = deepcopy(a)
    b["operations"][0]["terms"]["legs"][0]["quantity_milli"] = 900
    assert agreement(a, b)["operations"] == 0.5
    b["operations"][1]["quantity_milli"] = 100
    assert agreement(a, b)["operations"] == 0
    assert agreement(a, dict(a, operations=list(reversed(a["operations"]))))["operations"] == 0
    assert agreement(a, dict(a, operations=a["operations"] * 2))["operations"] == 0.5


def test_messages_are_separate_and_extra_forecasts_are_penalized():
    a = dict(empty(0, "a"), messages=[dict(channel="public", to=[], content="quota 10")])
    b = dict(a, messages=[dict(channel="public", to=[], content="quota 100")])
    assert agreement(a, b)["messages_exact"] == 0
    assert agreement(a, b)["score"] is None
    a["forecasts"] = [dict(probe_id="x", probabilities=[1, 0])]
    b["forecasts"] = a["forecasts"] + [dict(probe_id="y", probabilities=[1, 0])]
    assert agreement(a, b)["forecasts"] == 0.5


async def test_empty_reference_probes_do_not_award_free_points():
    probe = dict(id="p", domain="D4", tick=0, observation={"tick": 0, "agent_id": "a"},
                 oracle=empty(0, "a"), references={"reciprocal": empty(0, "a")})
    result = await screen(dict(probes=[probe]), OracleEcho(dict(probes=[probe])))
    assert result["agreement"] is None
    assert result["summary"]["D4"]["informative"] == 0
    assert result["summary"]["D4"]["validity"] == 1


def test_correlation_requires_full_runs_matching_configuration_and_keeps_ties(tmp_path):
    runner = ProbeRunner(tmp_path)
    runner.save_bank(dict(evaluation="e", id="bank", scorer=SCORER))
    reports = dict(scope="full", models={})
    for name, match, score in [("a", 0.5, 10), ("b", 0.5, 20), ("c", 0.9, 30)]:
        latest = dict(model_id=name, agreement=match, status="completed", scorer=SCORER,
                      bank_id="bank", model_hash=name, acquisition=ACQUISITION)
        write_json(tmp_path / "e" / f"screen-{name}.json", latest)
        reports["models"][name] = dict(score=score, model_hash=name)
    assert runner.correlation("e", reports, latest) == pytest.approx(0.8660254)
    assert runner.correlation("e", dict(reports, scope="smoke"), latest) is None
    assert runner.correlation("e", dict(reports, scope="pilot"), latest) is None
    reports["models"]["a"]["model_hash"] = "changed-prompt"
    assert runner.correlation("e", reports, latest) is None
    runner.save_bank(dict(evaluation="e", id="new-bank", scorer=SCORER))
    assert all(r["stale"] for r in runner.screens("e"))


def test_active_bank_cannot_be_replaced(tmp_path):
    runner = ProbeRunner(tmp_path)
    runner.active["e"] = {"model": object()}
    with pytest.raises(ValueError, match="active screens"):
        runner.save_bank(dict(evaluation="e", id="b", scorer=SCORER))
