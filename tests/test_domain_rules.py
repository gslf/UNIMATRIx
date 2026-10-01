"""Domain rules under the lenient, standard and harsh presets."""

import pytest

from tests.test_benchmark_core import setup
from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.runner import Runner
from unimatrix.core.visibility import observe
from unimatrix.policies.router import Router
from unimatrix.scenarios import get_scenario


def test_d1_standard_replaces_certified_truth_with_a_second_signal():
    scenario = get_scenario("D1")
    lenient = scenario.build(episode("D1", layers="lenient"))
    standard = scenario.build(episode("D1"))
    assert "second" not in lenient.scenario["windows"][1]
    assert "second" in standard.scenario["windows"][1] and "inspection" in standard.scenario["windows"][0]
    standard.tick, lenient.tick = 30, 30
    for state in (standard, lenient):
        state.scenario["window"] = 1
    focal = standard.scenario["focal"]
    assert observe(lenient, focal, scenario)["scenario"]["evidence_certified"] is True
    packet = observe(standard, focal, scenario)["scenario"]
    assert packet["evidence_certified"] is False
    assert packet["signal"] == standard.scenario["windows"][1]["second"]


def test_d2_standard_counterparties_require_a_surplus_share():
    scenario = get_scenario("D2")
    state = scenario.build(episode("D2"))
    market = state.scenario["markets"][0]
    assert market["reservation"] >= 1
    assert observe(state, market["seller"], scenario)["scenario"]["reservation"] == market["reservation"]
    assert "reservation" not in scenario.build(episode("D2", layers="lenient")).scenario["markets"][0]


def test_d3_standard_distributes_codes_by_skill():
    scenario = get_scenario("D3")
    state = scenario.build(episode("D3"))
    packets = {slot: observe(state, slot, scenario)["scenario"] for slot in state.agents}
    focal = state.scenario["focal"]
    assert packets[focal]["coordinator"] == focal and packets[focal]["keys_distributed"]
    held = [set(p["work_keys"]) for p in packets.values()]
    assert set().union(*held) == set(state.scenario["tasks"])
    assert all(len(keys) < len(state.scenario["tasks"]) for keys in held)


def test_d4_standard_hides_the_optimal_action_and_varies_demand():
    scenario = get_scenario("D4")
    state = scenario.build(episode("D4"))
    packet = observe(state, state.scenario["focal"], scenario)["scenario"]
    assert packet["available_actions"] == [] and "rules" in packet
    assert packet["collapse_threshold_milli"] > 0
    demands = {scenario.demand(state, s) for s in state.agents}
    assert demands <= set(range(100, 151))
    expected = sum(scenario.demand(state, s) for s in state.agents)
    events = scenario.evolve(state, state)
    assert next(e for e in events if e["type"] == "service_sampled")["payload"]["target"] == expected


async def test_d4_collapse_stops_regeneration():
    manifest = episode("D4", layers={"scarcity": {"collapse_threshold": 50}})
    scenario = get_scenario("D4")
    state = scenario.build(manifest)
    state.scenario["stock"] = state.scenario["capacity"] // 4
    events = scenario.evolve(state, state)
    assert next(e for e in events if e["type"] == "resource_regenerated")["payload"]["quantity_milli"] == 0


async def test_d5_execution_noise_slips_without_cost(tmp_path):
    manifest = episode("D5", layers={"noise": {"execution_error": 500}})
    scenario = get_scenario("D5")
    _, _, state, store = setup(tmp_path, "D5")
    store.close()
    from unimatrix.persistence.event_store import EventStore

    store = EventStore(tmp_path / "noisy.db")
    state = scenario.build(manifest)
    store.initialize(manifest, state)
    await Runner(store, scenario, Router.scripted(manifest)).run(40)
    counts = store.counts()
    assert counts.get("commitment_slipped", 0) > 0
    assert counts.get("commitment_fulfilled", 0) > 0
    store.close()


def test_d5_release_bonus_halves_unilateral_exit_credit():
    scenario = get_scenario("D5")
    for preset, expected in [("lenient", 1), ("standard", 0.5)]:
        state = scenario.build(episode("D5", layers=preset))
        window = state.scenario["opportunities"][0]
        window["cost"], window["value"] = 5, 2
        focal = state.scenario["focal"]
        scenario.resolve(state, state, {focal: {}}, focal, dict(verb="work", project_id="exit-0"), "x")
        assert window["credit"] == expected


async def test_d6_successor_brings_a_new_mandate(tmp_path):
    manifest = episode("D6")
    scenario = get_scenario("D6")
    from unimatrix.persistence.event_store import EventStore

    store = EventStore(tmp_path / "episode.db")
    state = scenario.build(manifest)
    before = [w["maximum"] for w in state.scenario["windows"]]
    store.initialize(manifest, state)
    shock = state.scenario["shock_tick"]
    final = await Runner(store, scenario, Router.scripted(manifest)).run(shock + 1)
    after = [w["maximum"] for w in final.scenario["windows"]]
    turn = shock // 40
    assert before[:turn] == after[:turn]
    assert all("successor_benefits" in w for w in final.scenario["windows"][turn + 1 :])
    assert not any("successor_benefits" in w for w in final.scenario["windows"][:turn])
    store.close()


def test_d7_standard_requires_explicit_teaching():
    scenario = get_scenario("D7")
    state = scenario.build(episode("D7"))
    assert observe(state, state.scenario["focal"], scenario)["scenario"]["teaching_required"]
    lenient = scenario.build(episode("D7", layers="lenient"))
    assert "teaching_required" not in observe(lenient, lenient.scenario["focal"], scenario)["scenario"]


def test_d8_examples_disappear_after_the_shock():
    scenario = get_scenario("D8")
    state = scenario.build(episode("D8"))
    slot = state.scenario["focal"]
    assert len(observe(state, slot, scenario)["scenario"]["examples"]) == 2
    state.tick = 130
    assert observe(state, slot, scenario)["scenario"]["examples"] == []
    harsh = scenario.build(episode("D8", layers="harsh"))
    assert observe(harsh, harsh.scenario["focal"], scenario)["scenario"]["examples"] == []


@pytest.mark.parametrize("domain", [f"D{i}" for i in range(1, 9)])
async def test_standard_and_harsh_episodes_complete_with_metrics(tmp_path, domain):
    from unimatrix.benchmark.scheduler import run_episode

    for preset in ["standard", "harsh"]:
        manifest = episode(domain, layers=preset)
        result = await run_episode(manifest, tmp_path / preset)
        assert result["completed_tick"] == 240
        assert all(0 <= m["normalized_value"] <= 1 for m in result["metrics"].values())
