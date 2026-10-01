"""Actors can use the published peer protocol without consulting private state."""

import json

import pytest

from tests.test_benchmark_core import step
from unimatrix.actions.resolver import resolve
from unimatrix.actions.schemas import empty, validate
from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.recipes import RecipeRepository, bind_candidate
from unimatrix.core.ids import canonical
from unimatrix.core.visibility import observe
from unimatrix.policies.scripted import Scripted
from unimatrix.research.measurement_quality import audit_recipe, balanced_bank
from unimatrix.scenarios import get_scenario


@pytest.mark.parametrize("domain", ["D3", "D8"])
async def test_documented_key_request_reaches_peers_and_codes_return(domain):
    scenario = get_scenario(domain)
    manifest = episode(domain, layers="standard")
    state = scenario.build(manifest)
    focal = manifest["focal_slot"]
    packet = observe(state, focal, scenario)
    assert '{"request":"work_keys"}' in packet["protocol"]
    decisions = {s: empty(0, s) for s in state.agents}
    decisions[focal]["messages"] = [dict(channel="public", to=[], content='{"request":"work_keys"}')]
    packets = {s: observe(state, s, scenario) for s in state.agents}
    state, _ = resolve(state, decisions, scenario, packets)
    packets = {s: observe(state, s, scenario) for s in state.agents}
    decisions = {focal: empty(state.tick, focal)}
    for slot, observation in packets.items():
        if slot != focal:
            raw, _ = await Scripted("reciprocal").decide(observation, {})
            decisions[slot] = validate(raw, state.tick, slot)
    state, _ = resolve(state, decisions, scenario, packets)
    replies = observe(state, focal, scenario)["inbox"]
    codes = {k: v for reply in replies for k, v in json.loads(reply["content"]).get("work_keys", {}).items()}
    assert codes
    assert set(codes) | set(packet["scenario"]["work_keys"]) == set(packet["scenario"]["tasks"])


async def test_published_manual_format_is_understood_by_the_learner():
    scenario = get_scenario("D7")
    manifest = episode("D7", layers="standard")
    state = scenario.build(manifest)
    teacher = manifest["focal_slot"]
    packet = observe(state, teacher, scenario)
    assert "input occupies slot 0" in packet["protocol"]
    assert '"procedures"' in packet["protocol"]
    procedures = packet["scenario"]["procedures"]
    state, events = step(state, scenario, {teacher: [dict(
        verb="publish", kind="procedure", content=canonical(dict(procedures=procedures)), parent_ids=[])]})
    artifact = next(e["payload"]["artifact_id"] for e in events if e["type"] == "artifact_published")
    learner = packet["scenario"]["learner"]
    state, _ = step(state, scenario, {teacher: [dict(verb="teach", artifact_id=artifact, recipient_id=learner)]})
    policy = Scripted("reciprocal")
    observed_success = False
    while state.tick < 40:
        observation = observe(state, learner, scenario)
        raw, _ = await policy.decide(observation, {})
        decision = validate(raw, state.tick, learner)
        packets = {s: observe(state, s, scenario) for s in state.agents}
        decisions = {s: empty(state.tick, s) for s in state.agents}
        decisions[learner] = decision
        state, events = resolve(state, decisions, scenario, packets)
        observed_success |= any(e["type"] == "heldout_task_resolved" and e["payload"]["success"] for e in events)
    assert observed_success


def test_balanced_control_design_includes_every_cell_at_every_seed():
    bank = balanced_bank(RecipeRepository().get("standard-v1"), list(range(30000, 30008)), ticks=240)
    coverage = audit_recipe(bank)
    assert coverage["fully_crossed"] and coverage["balanced_seed_blocks"]
    assert coverage["episodes"] == 256
    assert {c["domain"] for c in bank["cases"] if c["seed"] == 30000} == {f"D{i}" for i in range(1, 9)}


def test_complete_protocol_fits_the_existing_observation_budget():
    bank = balanced_bank(RecipeRepository().get("standard-v1"), [991, 992], ticks=240)
    for manifest in bind_candidate(bank, "reciprocal")["episodes"]:
        scenario = get_scenario(manifest["domain"])
        state = scenario.build(manifest)
        for slot in state.agents:
            packet = observe(state, slot, scenario)
            assert len(canonical(packet).encode()) <= 24000

            other = state.clone()
            other.scenario["private_test_secret"] = "secret-not-visible"
            assert observe(other, slot, scenario)["protocol"] == packet["protocol"]
