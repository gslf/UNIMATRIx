import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tests.test_benchmark_core import setup, step
from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.scheduler import run_episode
from unimatrix.core.visibility import observe
from unimatrix.scenarios import get_scenario
from unimatrix.scenarios.d2 import enumerate_market, utility
from unimatrix.scenarios.d4 import regeneration
from unimatrix.scenarios.layers import PRESETS
from unimatrix.world.contracts import Rejected
from unimatrix.world.recipes import execute


def test_market_fixture_exhaustive():
    m = get_scenario("D2").build(episode()).scenario["markets"][0]
    seller, buyer = m["seller"], m["buyer"]
    assert len(m["parties"]) == 3
    assert len(enumerate_market(m)) == 396
    assert m["gmax"] == 11
    assert m["bounds"][buyer] == [0, 23]
    assert m["bounds"][seller] == [0, 12]
    assert utility(m, seller, {"goods": 0, "credits": 6000}) == 6
    assert utility(m, buyer, {"goods": 2000, "credits": 4000}) == 17


def test_ecology_maximum_and_reference_feasible():
    assert regeneration(50000) == 2000
    assert regeneration(0) == regeneration(100000) == 0
    for preset in PRESETS:
        manifest = episode("D4", layers=preset)
        assert get_scenario("D4").feasible(manifest)["reference_terminal_stock"] >= 40000


def test_recipe_rejects_cycles_and_unknown_transforms():
    with pytest.raises(Rejected):
        execute([dict(transform_id="x", input_slots=[0], output_slot=0)], [0], {"x": [1, 2, 3, 0]})
    with pytest.raises(Rejected):
        execute([dict(transform_id="shell", input_slots=[0], output_slot=1)], [0], {})


def test_sealed_d1_targets_never_observed():
    scenario = get_scenario("D1")
    state = scenario.build(episode("D1"))
    focal = state.scenario["focal"]
    packet = observe(state, focal, scenario)
    state.scenario["windows"][1]["target"] ^= 1
    assert observe(state, focal, scenario) == packet
    state.tick = 18
    forecasts = [dict(probe_id="fact-0", probabilities=[0.25, 0.75])]
    scenario.forecasts(state, state.clone(), focal, forecasts)
    state.tick = 19
    scenario.forecasts(state, state.clone(), focal, [dict(probe_id="fact-0", probabilities=[1, 0])])
    assert state.scenario["windows"][0]["forecasts"]["fact-0"] == [0.25, 0.75]


def test_dependent_work_cannot_see_contemporary_completion():
    scenario = get_scenario("D3")
    state = scenario.build(episode("D3"))
    slot = next(iter(state.agents))
    state.agents[slot]["location"] = "workshop-1"
    state.agents[slot]["mandate"]["skill"] = 1
    result, _ = step(
        state,
        scenario,
        {
            slot: [
                dict(
                    verb="work",
                    project_id="task-1:" + state.scenario["tasks"]["task-1"]["access_code"],
                )
            ]
        },
    )
    assert result.receipts[slot][0]["reason"] == "unmet_dependencies"


def test_allocation_reserved_against_transfer():
    scenario = get_scenario("D6")
    state = scenario.build(episode("D6"))
    owner, peer = state.scenario["owners"]
    choice = max(scenario.observation(state, owner)["choices"], key=lambda c: c["terms"]["cost"])
    after, _ = step(
        state,
        scenario,
        {
            owner: [
                dict(verb="commit", opportunity_id=choice["id"], terms_hash=choice["terms_hash"]),
                dict(
                    verb="transfer", recipient_id=peer, resource_id="budget", quantity_milli=10000
                ),
            ]
        },
    )
    assert after.receipts[owner][0]["status"] == "executed"
    assert after.receipts[owner][1]["status"] == "rejected"


def test_event_contract(tmp_path):
    _, _, _, store = setup(tmp_path)
    schema = json.loads(
        (
            Path(__file__).parents[1] / "tests/fixtures/blueprint/contracts/event.schema.json"
        ).read_text()
    )
    validator = Draft202012Validator(schema)
    for event in store.events():
        validator.validate(event)
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", [f"D{i}" for i in range(1, 9)])
async def test_domain_completes_and_metrics_have_evidence(tmp_path, domain):
    manifest = episode(domain)
    result = await run_episode(manifest, tmp_path)
    assert result["completed_tick"] == 240

    assert (tmp_path / manifest["run_id"] / "episode.db").stat().st_size < 12_000_000
    assert len(result["metrics"]) == 3
    for metric in result["metrics"].values():
        assert 0 <= metric["normalized_value"] <= 1
        assert metric["denominator"] > 0
        assert metric["evidence_event_ids"]
    assert not result["certified"]


@pytest.mark.asyncio
async def test_coordination_needs_communication(tmp_path):
    from unimatrix.core.ids import digest

    normal = episode("D3", candidate="coordinator")
    muted = dict(normal, ablations=["no_communication"])
    muted["run_id"] = digest({k: v for k, v in muted.items() if k != "run_id"})[:24]
    a = await run_episode(normal, tmp_path)
    b = await run_episode(muted, tmp_path)
    assert (
        a["metrics"]["D3.completion"]["normalized_value"]
        > b["metrics"]["D3.completion"]["normalized_value"]
    )
    assert b["metrics"]["D3.completion"]["normalized_value"] == 0


def test_transmission_has_disjoint_inputs_and_hides_solutions():
    scenario = get_scenario("D7")
    state = scenario.build(episode("D7", layers="harsh"))
    tasks = state.scenario["tasks"]
    keys = [(tuple(t["chain"]), t["input"]) for t in tasks]
    assert len(keys) == len(set(keys))
    assert all(t["input"] != 0 for t in tasks)
    state.tick = 30
    packet = scenario.observation(state, state.scenario["learner"])
    assert packet["tasks"]
    assert all("target" not in t and "chain" not in t for t in packet["tasks"])
    assert packet["transforms"] is None and packet["procedures"] is None


def test_constitution_cannot_claim_external_assets():
    scenario = get_scenario("D6")
    state = scenario.build(episode("D6"))
    owner, other = state.scenario["owners"]
    state, _ = step(
        state, scenario, {owner: [dict(verb="create_group", name="council", purpose="allocate")]}
    )
    group = next(k for k, v in state.objects.items() if v["kind"] == "group")
    rule = dict(
        decision_method="majority",
        quorum_numerator=1,
        quorum_denominator=2,
        authorized_asset_ids=["budget-" + other],
        spend_limit_milli=20000,
        expiry_state=40,
    )
    after, _ = step(
        state, scenario, {owner: [dict(verb="propose_rule", group_id=group, rule=rule)]}
    )
    assert after.receipts[owner][0]["reason"] == "unauthorized_asset"
    assert after.objects[group]["rule"] is None


def test_single_member_constitution_and_rotating_authority():
    scenario = get_scenario("D6")
    state = scenario.build(episode("D6"))
    owner = state.scenario["owners"][0]
    state, _ = step(
        state, scenario, {owner: [dict(verb="create_group", name="council", purpose="allocate")]}
    )
    group = next(k for k, v in state.objects.items() if v["kind"] == "group")
    rule = dict(
        decision_method="rotation",
        quorum_numerator=1,
        quorum_denominator=1,
        authorized_asset_ids=["budget-" + owner],
        spend_limit_milli=10000,
        expiry_state=40,
    )
    after, events = step(
        state, scenario, {owner: [dict(verb="propose_rule", group_id=group, rule=rule)]}
    )
    assert after.objects[group]["rule"] == rule
    assert any(e["type"] == "rule_adopted" for e in events)


def test_turnover_drops_previous_occupants_private_tick_events():
    scenario = get_scenario("D7")
    state = scenario.build(episode("D7"))
    learner = state.scenario["learner"]
    state.tick = state.scenario["shock_tick"] - 1
    state.agents[learner]["note"] = "old occupant private note"
    state.objects["private-book"] = dict(
        kind="artifact",
        owner=learner,
        visibility=[learner],
        content="old private knowledge",
        read_by=[],
    )
    after, _ = step(state, scenario, {learner: [dict(verb="inspect", target_id="private-book")]})
    assert after.agents[learner]["generation"] == 1
    assert after.agents[learner]["note"] == ""
    assert not any(m.get("type") == "artifact_read" for m in after.memories[learner])
    assert "private-book" not in after.objects
