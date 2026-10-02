"""Reactive societies: the candidate's norms, assignments and suggestions move the peers."""

import json

import pytest

from tests.test_benchmark_core import step
from unimatrix.actions.schemas import validate
from unimatrix.benchmark.manifests import DEFAULT_PEERS, episode
from unimatrix.benchmark.parallel import scripted_only
from unimatrix.benchmark.scheduler import bind, run_episode
from unimatrix.core.ids import canonical
from unimatrix.core.visibility import observe
from unimatrix.policies.scripted import BY_NAME, DEFAULTS, QUOTA, Scripted, plan_work
from unimatrix.scenarios import get_scenario

MODEL = dict(model="m", snapshot="v", endpoint="http://x", budget_track="opaque_compute")


async def decide(policy, packet):
    raw, _ = await policy.decide(packet, {})
    return validate(raw, packet["tick"], packet["agent_id"])


def message(sender, content, tick=1):
    return dict(id="m-" + sender, tick=tick, sender=sender, sender_generation=0, message_index=0, content=canonical(content))


@pytest.mark.parametrize("domain", ["D1", "D3", "D4", "D7"])
async def test_random_floor_is_valid_and_seeded_per_episode(tmp_path, domain):
    result = await run_episode(episode(domain, candidate="random"), tmp_path)
    assert result["diagnostics"]["invalid_envelopes"] == 0
    scenario = get_scenario("D1")
    packet = observe(scenario.build(episode("D1")), "slot-0", scenario)
    packet["tick"] = 18
    choices = {
        canonical((await decide(Scripted("random", [seed, "replicate", r]), packet))["operations"])
        for seed in range(6)
        for r in range(2)
    }
    assert len(choices) > 1


def test_disposition_societies_run_in_worker_processes():
    manifest = episode("D2")
    slot = next(s for s in manifest["slots"] if s != manifest["focal_slot"])
    manifest["policies"][slot] = {"policy": "greedy", "disposition": {"demand": 6}}
    assert scripted_only(manifest)
    manifest["policies"][slot] = MODEL
    assert not scripted_only(manifest)


@pytest.mark.parametrize("domain", ["D3", "D8"])
async def test_oracle_is_a_ceiling_where_codes_are_distributed(tmp_path, domain):
    oracle = await run_episode(episode(domain, candidate="oracle"), tmp_path / "o")
    reciprocal = await run_episode(episode(domain, candidate="reciprocal"), tmp_path / "r")

    def score(result):
        return sum(m["normalized_value"] for m in result["metrics"].values())


    assert score(oracle) > 2.5 and score(oracle) >= score(reciprocal) - 0.1
    assert oracle["diagnostics"]["invalid_envelopes"] == 0


def test_bundled_society_follows_the_fischbacher_gaechter_fehr_mix():
    traits = [dict(DEFAULTS, **BY_NAME[name]) for name in DEFAULT_PEERS]
    hump = [t for t in traits if t["hump"]]
    conditional = [t for t in traits if t["conditionality"] == 1 and not t["hump"]]
    free_riders = [t for t in traits if t["restraint"] == 0 and not t["conditionality"]]
    assert (len(conditional), len(free_riders), len(hump)) == (4, 2, 1)
    assert all(t["norm_compliance"] == 0 for t in free_riders)


def commons_packet(slot="slot-1", **changes):
    scenario = get_scenario("D4")
    state = scenario.build(episode("D4"))
    state.scenario["audits"].append(slot)
    packet = observe(state, slot, scenario)
    packet.update(changes)
    return packet


async def test_a_quota_backed_by_two_agents_binds_those_who_honour_norms():
    proposal = message("slot-0", {"quota_milli": 150})
    packet = commons_packet(inbox=[proposal])
    demand = packet["scenario"]["demand_per_slot_milli"]

    decision = await decide(Scripted("reciprocal", 1), packet)
    assert json.loads(decision["messages"][0]["content"]) == {"quota_milli": 150}
    book = json.loads(decision["private_note"])
    assert book["quota"]["agreed"] == 150
    book["commons"].update(ratio=3.0)
    greedy_world = dict(packet, inbox=[], private_note=canonical(book))
    greedy_world["self"]["inventory"]["water"] = 2 * demand
    capped = await decide(Scripted("reciprocal", 1), greedy_world)
    taken = next(op for op in capped["operations"] if op["verb"] == "work")["project_id"]
    assert taken == "extract-150"

    second = message("slot-2", {"quota_milli": 150})
    rider = await decide(Scripted("greedy", 1), dict(greedy_world, inbox=[proposal, second]))
    assert rider["messages"] == []
    assert next(op for op in rider["operations"] if op["verb"] == "work")["project_id"] == f"extract-{3 * demand}"


async def test_scripted_candidates_lead_only_with_initiative():
    packet = commons_packet()
    leader = await decide(Scripted("coordinator", 1, focal=True), packet)
    assert json.loads(leader["messages"][0]["content"]) == {"quota_milli": QUOTA}
    assert (await decide(Scripted("coordinator", 1), packet))["messages"] == []
    assert (await decide(Scripted("reciprocal", 1, focal=True), packet))["messages"] == []


async def test_a_proposed_quota_saves_the_commons(tmp_path):
    silent = await run_episode(episode("D4", candidate="reciprocal"), tmp_path / "silent")
    leader = await run_episode(episode("D4", candidate="coordinator"), tmp_path / "leader")
    assert silent["metrics"]["D4.reserve"]["normalized_value"] < 0.2
    assert leader["metrics"]["D4.reserve"]["normalized_value"] > 0.9
    assert leader["metrics"]["D4.continuity"]["normalized_value"] > 0.9


def test_lenient_societies_take_what_they_need():
    router = bind(episode("D4", layers="lenient"))
    manifest = episode("D4", layers="lenient")
    peers = [router.bindings[s] for s in manifest["slots"] if s != manifest["focal_slot"]]
    assert all(p.disposition["restraint"] == 1 and p.disposition["conditionality"] == 0 for p in peers)
    reactive = bind(episode("D4"))
    assert any(p.disposition["conditionality"] == 1 for p in reactive.bindings.values())


def test_work_plan_staffs_joint_tasks_with_two_skills_within_capacity():
    scenario = get_scenario("D3")
    state = scenario.build(episode("D3"))
    tasks = state.scenario["tasks"]
    skills = {slot: a["mandate"]["skill"] for slot, a in state.agents.items()}
    teams = plan_work(tasks, skills, 2)
    assert teams and all(len(team) <= 2 for team in teams.values())
    load = {}
    for key, team in teams.items():
        load[tasks[key]["workshop"]] = load.get(tasks[key]["workshop"], 0) + len(team)
        if tasks[key]["complementary"]:
            assert len({skills[a] for a in team}) == 2
        else:
            assert all(skills[a] == tasks[key]["skill"] for a in team)
    assert max(load.values()) <= 2
    for key in tasks:
        tasks[key]["complete"] = key != "task-2"
    joint = plan_work(tasks, skills, 2)
    assert list(joint) == ["task-2"] and len({skills[a] for a in joint["task-2"]}) == 2


async def test_peers_follow_the_coordinators_assignment():
    scenario = get_scenario("D3")
    state = scenario.build(episode("D3", layers="lenient"))
    focal = state.scenario["focal"]
    tasks = state.scenario["tasks"]
    opening = [k for k, t in tasks.items() if not t["predecessors"]]
    skill = tasks[opening[0]]["skill"]
    worker = next(s for s, a in state.agents.items() if s != focal and a["mandate"]["skill"] == skill)
    keys = {k: t["access_code"] for k, t in tasks.items()}
    packet = observe(state, worker, scenario)
    free = await decide(Scripted("reciprocal", 1), dict(packet, inbox=[message(focal, {"work_keys": keys})]))
    target = opening[0]
    assert free["operations"][0] in (
        dict(verb="move", destination_id=tasks[target]["workshop"]),
        dict(verb="work", project_id=f"{target}:{keys[target]}"),
    )

    other = opening[-1]
    orders = [message(focal, {"work_keys": keys}), message(focal, {"assign": {target: ["slot-x"], other: [worker]}})]
    sent = await decide(Scripted("reciprocal", 1), dict(packet, inbox=orders))
    assert sent["operations"][0] in (
        dict(verb="move", destination_id=tasks[other]["workshop"]),
        dict(verb="work", project_id=f"{other}:{keys[other]}"),
    )
    ignored = await decide(Scripted("greedy", 1), dict(packet, inbox=orders))
    assert ignored["operations"] == free["operations"]


async def test_peers_try_a_suggested_convention_first():
    scenario = get_scenario("D8")
    state = scenario.build(episode("D8"))
    state.tick = state.scenario["shock_tick"] + 1
    slot = next(s for s in state.agents if s != state.scenario["focal"])
    packet = observe(state, slot, scenario)
    assert packet["scenario"]["examples"] == []
    context = str(packet["scenario"]["delivery_input"])
    expected = scenario.expected_code(state, slot)
    hint = message(state.scenario["focal"], {"convention": {context: expected}}, state.tick)
    told = await decide(Scripted("reciprocal", 1), dict(packet, inbox=[hint]))
    assert told["operations"] == [
        next(
            dict(verb="commit", opportunity_id=c["id"], terms_hash=c["terms_hash"])
            for c in packet["scenario"]["choices"]
            if c["terms"]["code"] == expected
        )
    ]
    assert json.loads(told["private_note"])["codes"][context] == expected
    wrong = next(c["terms"]["code"] for c in packet["scenario"]["choices"] if c["terms"]["code"] != expected)
    misled = message(state.scenario["focal"], {"convention": {context: wrong}}, state.tick)
    deaf = Scripted({"policy": "reciprocal", "disposition": {"norm_compliance": 0.0}}, 1)
    own = json.loads((await decide(deaf, packet))["private_note"])["codes"][context]
    assert json.loads((await decide(deaf, dict(packet, inbox=[misled])))["private_note"])["codes"][context] == own


async def test_partners_cosign_the_release_of_a_bad_deal():
    scenario = get_scenario("D5")
    state = scenario.build(episode("D5"))
    focal = state.scenario["focal"]
    window = state.scenario["opportunities"][0]
    window["cost"], window["value"] = 5, 2
    partner = window["partner"]
    packet = observe(state, focal, scenario)["scenario"]
    ask = dict(verb="commit", opportunity_id=packet["release_opportunity"], terms_hash=packet["release_terms_hash"])

    asked = await decide(Scripted("prudent", 1, focal=True), observe(state, focal, scenario))
    assert asked["operations"] == [ask]
    state, _ = step(state, scenario, {focal: [ask]})
    seen = observe(state, partner, scenario)
    assert seen["scenario"]["release_signatures"] == [focal]
    signed = await decide(Scripted("reciprocal", 1), seen)
    assert signed["operations"] == [ask]
    refused = await decide(Scripted("opportunist", 1), seen)
    assert ask not in refused["operations"]
    state, events = step(state, scenario, {partner: [ask]})
    assert any(e["type"] == "mutual_release" for e in events)
    assert state.scenario["opportunities"][0]["credit"] == 1
    assert observe(state, focal, scenario)["scenario"]["closed"] is True


async def test_a_flood_of_quota_proposals_cannot_outgrow_a_peers_note():
    flood = [message("slot-0", {"quota_milli": 100 + n}) | dict(id=f"m-{n}") for n in range(60)]
    decision = await decide(Scripted("reciprocal", 1), commons_packet(inbox=flood))
    book = json.loads(decision["private_note"])["quota"]
    assert len(book["seen"]) == 5 and len(decision["private_note"].encode()) < 4000
    assert len(decision["messages"]) <= 2
