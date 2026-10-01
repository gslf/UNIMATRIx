"""Variability with explicit recipes: moving shocks, parallel worlds, knob ranges, events, networks, roles."""

import pytest

from tests.test_benchmark_core import step
from tests.test_research import design
from unimatrix.actions.resolver import resolve
from unimatrix.actions.schemas import empty
from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.recipes import RecipeRepository, validate_recipe
from unimatrix.benchmark.scheduler import run_episode
from unimatrix.benchmark.validation import validate_policy
from unimatrix.core.random_tape import noise_tape
from unimatrix.core.visibility import observe
from unimatrix.policies.llm_policy import LLMPolicy
from unimatrix.research.campaigns import smoke_cases
from unimatrix.research.design import Design, build_design
from unimatrix.scenarios import get_scenario
from unimatrix.scenarios.layers import layers_key, resolve_layers, shock_tick, validate_events

MODEL = dict(
    model="m", snapshot="v", endpoint="http://x", context_bytes_verified=24000, budget_track="opaque_compute"
)


def test_shocks_and_turnovers_move_with_the_seed():
    standard, lenient = resolve_layers("standard"), resolve_layers("lenient")
    ticks = {shock_tick(seed, standard) for seed in range(40)}
    assert len(ticks) > 5 and min(ticks) >= 100 and max(ticks) <= 140
    assert {shock_tick(seed, lenient) for seed in range(10)} == {120}
    state = get_scenario("D8").build(episode("D8", seed=3))
    shock = state.scenario["shock_tick"]
    assert shock == shock_tick(3, standard)
    assert state.scenario["essential_deadlines"][-1] == 240

    packet = observe(state, state.scenario["focal"], get_scenario("D8"))["scenario"]
    assert packet["announced_shock_state"] is None and packet["essential_deadlines"] == []


@pytest.mark.parametrize("domain,metric", [("D6", "D6.turnover_service"), ("D8", "D8.postshock_service")])
async def test_extractor_denominators_follow_the_real_tick(tmp_path, domain, metric):
    manifest = episode(domain, seed=5)
    shock = shock_tick(5, manifest["layers"])
    assert shock != 120
    result = await run_episode(manifest, tmp_path)
    assert result["metrics"][metric]["denominator"] == 240 - shock
    if domain == "D8":
        expected = sum((tick + 1) % 10 == 0 for tick in range(shock + 9, 240))
        assert result["metrics"]["D8.convention_transfer"]["denominator"] == expected


def test_replicates_are_parallel_worlds():
    scenario = get_scenario("D1")
    first = scenario.build(episode("D1", seed=7, replicate=0)).scenario
    second = scenario.build(episode("D1", seed=7, replicate=1)).scenario
    assert [w["target"] for w in first["windows"]] == [w["target"] for w in second["windows"]]
    assert [w["signals"] for w in first["windows"]] != [w["signals"] for w in second["windows"]]
    commons = get_scenario("D4")
    a, b = (commons.build(episode("D4", seed=7, replicate=r)) for r in (0, 1))
    assert a.scenario["demand"] != b.scenario["demand"]
    slots = list(a.agents)
    orders = {tuple(noise_tape(state).priority(3, slots)) for state in (a, b)}
    assert len(orders) == 2
    assert episode("D4", seed=7)["focal_slot"] == episode("D4", seed=7, replicate=1)["focal_slot"]


def ranged():
    entry = {"preset": "standard", "family": "wide", "noise": {"execution_error": [30, 150]}, "scarcity": {"collapse_threshold": [3, 9]}}
    data = design(complexity=["standard", entry])
    data.update(domains=["D4", "D5"], seeds=[100, 101, 102, 103], roles=["advantaged", "disadvantaged"], replicates=2, holdout_seeds=[])
    return data


def test_ranged_knobs_are_drawn_at_build_into_one_family():
    data = ranged()
    result = build_design(Design(**data), RecipeRepository())
    spec = validate_recipe(result["plan"])
    family = [c for c in spec["cases"] if not isinstance(c["layers"], str)]
    assert len(family) == 2 * 4 * 2 * 2 and {layers_key(c["layers"]) for c in family} == {"wide"}
    draws = {}
    for case in family:
        value = (case["layers"]["noise"]["execution_error"], case["layers"]["scarcity"]["collapse_threshold"])
        assert 30 <= value[0] <= 150 and 3 <= value[1] <= 9

        assert draws.setdefault((case["domain"], case["seed"]), value) == value
    assert len(set(draws.values())) > 1
    assert build_design(Design(**data), RecipeRepository())["plan"] == spec
    smoke = smoke_cases(spec)["cases"]
    assert sorted((c["domain"], layers_key(c["layers"])) for c in smoke) == [
        ("D4", "standard"), ("D4", "wide"), ("D5", "standard"), ("D5", "wide"),
    ]
    assert any("family" in note for note in result["notes"])


@pytest.mark.parametrize(
    "entry",
    [
        {"noise": {"execution_error": [150, 30]}},
        {"noise": {"execution_error": [0, 9000]}},
        {"noise": {"execution_error": [1, 2, 3]}},
        {"family": "standard", "noise": {"execution_error": [1, 2]}},
        {"family": "Not Valid", "noise": {"execution_error": [1, 2]}},
    ],
)
def test_invalid_ranges_and_families_are_rejected(entry):
    with pytest.raises(ValueError):
        build_design(Design(**design(complexity=[entry])), RecipeRepository())


def event_layers(*rules, **extra):
    return dict(extra, shock={"events": list(rules)})


def run_ticks(manifest, ticks):
    scenario = get_scenario(manifest["domain"])
    state = scenario.build(manifest)
    log = []
    for _ in range(ticks):
        decisions = {s: empty(state.tick, s) for s in state.agents}
        state, events = resolve(state, decisions, scenario, {s: dict(inbox=[]) for s in decisions})
        log += [(state.tick, e["type"], e["payload"]) for e in events]
    return state, log


def test_events_fire_on_time_by_chance_and_on_state():
    drought = dict(name="dry spell", when={"at": 5}, effect={"type": "layer", "layer": "scarcity", "key": "regeneration_rate", "value": 0, "duration": 3})
    storm = dict(name="storm", when={"chance": 1000, "from": 8, "to": 20}, effect={"type": "resource", "target": "commons", "resource": "water", "percent": 50})
    panic = dict(name="panic", when={"metric": "stock", "below": 45}, effect={"type": "turnover", "target": "peer"})
    manifest = episode("D4", layers=event_layers(drought, storm, panic))
    state, log = run_ticks(manifest, 12)
    fired = {p["name"]: tick for tick, kind, p in log if kind == "condition_triggered"}

    assert fired["dry spell"] == 5 and fired["storm"] == 8 and fired["panic"] == 9
    growth = {tick: p["quantity_milli"] for tick, kind, p in log if kind == "resource_regenerated"}

    assert growth[5] > 0 and growth[6] == growth[7] == growth[8] == 0 and growth[9] > 0
    assert [p["name"] for _, kind, p in log if kind == "condition_ended"] == ["dry spell"]
    assert state.scenario["layers"]["scarcity"]["regeneration_rate"] == 100
    assert sum(a["generation"] for a in state.agents.values()) == 1
    assert state.agents[state.scenario["focal"]]["generation"] == 0

    assert len(fired) == sum(kind == "condition_triggered" for _, kind, _ in log)


def test_domain_effects_and_the_partner_turnover():
    fault = dict(name="fault", when={"at": 2}, effect={"type": "fault", "task": "task-0"})
    state, log = run_ticks(episode("D3", layers=event_layers(fault)), 3)
    assert ("shock_applied", {"task_id": "task-0"}) in [(kind, p) for _, kind, p in log]
    shift = dict(name="shift", when={"at": 2}, effect={"type": "convention"})
    manifest = episode("D8", layers=event_layers(shift))
    scenario = get_scenario("D8")
    before = scenario.expected_code(scenario.build(manifest), manifest["focal_slot"])
    state, _ = run_ticks(manifest, 3)
    assert state.scenario["shifts"] > 0
    state.tick = 0
    assert scenario.expected_code(state, manifest["focal_slot"]) != before
    leave = dict(name="leave", when={"at": 2}, effect={"type": "turnover", "target": "partner"})
    state, _ = run_ticks(episode("D7", layers=event_layers(leave)), 3)
    assert state.agents[state.scenario["learner"]]["generation"] == 1


@pytest.mark.parametrize(
    "rule",
    [
        dict(name="x", when={"at": 0}, effect={"type": "convention"}),
        dict(name="x", when={"at": 5, "chance": 5}, effect={"type": "convention"}),
        dict(name="x", when={"metric": "mood", "below": 1}, effect={"type": "convention"}),
        dict(name="x", when={"metric": "stock"}, effect={"type": "convention"}),
        dict(name="x", when={"at": 5}, effect={"type": "earthquake"}),
        dict(name="x", when={"at": 5}, effect={"type": "layer", "layer": "pressure", "key": "competitor", "value": False}),
        dict(name="x", when={"at": 5}, effect={"type": "layer", "layer": "noise", "key": "execution_error", "value": 9000}),
        dict(name="x", when={"from": 9, "to": 3, "chance": 5}, effect={"type": "convention"}),
        dict(name="", when={"at": 5}, effect={"type": "convention"}),
    ],
)
def test_invalid_events_are_rejected(rule):
    with pytest.raises(ValueError):
        validate_events([rule])


async def test_events_are_part_of_the_feasibility_check(tmp_path):
    drain = dict(name="drain", when={"at": 200}, effect={"type": "resource", "target": "commons", "resource": "water", "percent": 0})
    with pytest.raises(Exception, match="feasib"):
        await run_episode(episode("D4", layers=event_layers(drain)), tmp_path)


def test_networks_limit_who_hears_whom():
    scenario = get_scenario("D1")
    ring = scenario.build(episode("D1", layers={"society": {"network": "ring"}}))
    slots = list(ring.agents)
    assert scenario.contacts(ring, slots[0]) == {slots[1], slots[-1]}
    hub = scenario.build(episode("D1", layers={"society": {"network": "hub"}}))
    focal = hub.scenario["focal"]
    other = next(s for s in slots if s != focal)
    assert scenario.contacts(hub, focal) == set(slots) - {focal} and scenario.contacts(hub, other) == {focal}
    clusters = scenario.build(episode("D1", layers={"society": {"network": "clusters"}}))
    assert scenario.contacts(clusters, slots[0]) == {slots[1], slots[2], slots[3]}
    assert slots[4] in scenario.contacts(clusters, slots[3])
    decisions = {s: empty(0, s) for s in slots}
    decisions[slots[0]]["messages"] = [
        dict(channel="public", to=[], content="hello"),
        dict(channel="private", to=[slots[4]], content="too far"),
    ]
    packets = {s: observe(ring, s, scenario) for s in slots}
    after, _ = resolve(ring, decisions, scenario, packets)
    heard = {s for s in slots if any(m["content"] == "hello" for m in after.inbox[s])}
    assert heard == {slots[1], slots[-1]}
    assert not any(m["content"] == "too far" for m in after.inbox[slots[4]])
    assert dict(status="rejected", reason="unavailable_recipient") in after.receipts[slots[0]]
    flags = {p["id"]: p["contact"] for p in packets[slots[0]]["peers"]}
    assert flags[slots[1]] and not flags[slots[4]]
    assert "contact" not in observe(scenario.build(episode("D1")), slots[0], scenario)["peers"][0]


def with_role(domain, fields, policy="reciprocal", **kwargs):
    manifest = episode(domain, **kwargs)
    slot = next(s for s in reversed(manifest["slots"]) if s != manifest["focal_slot"])
    manifest["policies"][slot] = dict(policy=policy, **fields)
    return manifest, slot


def test_roles_carry_resources_and_information():
    validate_policy({"policy": "greedy", "role": "Hoarders", "endowment": 50, "informed": True})
    validate_policy(dict(MODEL, role="Elders", goal="Keep the peace.", briefing="The stock regenerates.", endowment=200))
    for bad in [
        {"policy": "greedy", "endowment": 5},
        {"policy": "greedy", "endowment": 50.0},
        {"policy": "greedy", "informed": "yes"},
        {"policy": "greedy", "role": ""},
        {"policy": "greedy", "goal": "scripted peers take goals from dispositions"},
        dict(MODEL, goal="x" * 601),
    ]:
        with pytest.raises(ValueError):
            validate_policy(bad)
    manifest, slot = with_role("D3", {"endowment": 50, "informed": True, "role": "Foremen"})
    scenario = get_scenario("D3")
    state = scenario.build(manifest)
    assert state.agents[slot]["inventory"] == dict(material=50000, energy=50000)
    assert set(observe(state, slot, scenario)["scenario"]["work_keys"]) == set(state.scenario["tasks"])

    manifest, slot = with_role("D1", {"informed": True}, policy="independent")
    assert get_scenario("D1").build(manifest).scenario["accuracies"][slot] == 90
    manifest, slot = with_role("D4", {"informed": True})
    commons = get_scenario("D4")
    assert observe(commons.build(manifest), slot, commons)["scenario"]["stock"] is not None


def test_model_roles_receive_objectives_and_briefing():
    policy = LLMPolicy(dict(MODEL, system_prompt="You are calm.", goal="Keep the reserve.", briefing="Two peers free-ride."))
    prompt = policy.system_prompt()
    assert "Agent personality:\nYou are calm." in prompt
    assert "Objectives:\nKeep the reserve." in prompt and "Private information:\nTwo peers free-ride." in prompt
    from unimatrix.core.visibility import PROTOCOL
    assert LLMPolicy(MODEL).system_prompt() == PROTOCOL


def test_turnover_wipes_experience_but_not_the_slot():
    leave = dict(name="leave", when={"at": 1}, effect={"type": "turnover", "target": "partner"})
    manifest = episode("D5", layers=event_layers(leave))
    scenario = get_scenario("D5")
    state = scenario.build(manifest)
    partner = state.scenario["opportunities"][0]["partner"]
    state.agents[partner]["note"] = "remembers everything"
    after, events = step(state, scenario, {})
    assert after.agents[partner]["generation"] == 1 and after.agents[partner]["note"] == ""
    assert any(e["type"] == "slot_replaced" for e in events)
