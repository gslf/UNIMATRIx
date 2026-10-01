"""A peer must not co-sign an allocation whose ID prefixes the proposal's ID."""

import json

import pytest

from unimatrix.actions.resolver import resolve
from unimatrix.actions.schemas import empty, validate
from unimatrix.benchmark.manifests import episode
from unimatrix.core.visibility import observe
from unimatrix.policies.scripted import Scripted
from unimatrix.scenarios import get_scenario


def proposal_case():
    manifest = episode("D6", seed=43001, ticks=72)
    scenario = get_scenario("D6")
    state = scenario.build(manifest)
    focal, peer = state.scenario["owners"]
    return scenario, state, focal, peer


@pytest.mark.parametrize("content", [
    "Please sign allocation-0-10.",
    '{"allocation":"allocation-0-10"}',
    "Accept (allocation-0-10), please.",
])
def test_proposals_match_the_entire_allocation_id(content):
    scenario, state, focal, peer = proposal_case()
    packet = observe(state, peer, scenario)
    packet["inbox"] = [dict(sender=focal, content=content)]
    assert Scripted("reciprocal").allocation(packet)["id"] == "allocation-0-10"


@pytest.mark.parametrize("content", [
    "allocation-0-100",
    "prefixallocation-0-10",
    "allocation-0-10-suffix",
    "allocation-0-10_suffix",
])
def test_unknown_or_embedded_identifiers_do_not_become_valid_proposals(content):
    scenario, state, focal, peer = proposal_case()
    packet = observe(state, peer, scenario)
    default = Scripted("reciprocal").allocation(packet)["id"]
    assert default != "allocation-0-1"
    packet["inbox"] = [dict(sender=focal, content=content)]
    assert Scripted("reciprocal").allocation(packet)["id"] == default


async def test_exact_proposal_produces_matching_signatures_and_execution():
    scenario, state, focal, peer = proposal_case()
    packets = {s: observe(state, s, scenario) for s in state.agents}
    target = next(c for c in packets[focal]["scenario"]["choices"] if c["id"] == "allocation-0-10")
    decisions = {s: empty(0, s) for s in state.agents}
    decisions[focal]["operations"] = [dict(verb="commit", opportunity_id=target["id"], terms_hash=target["terms_hash"])]
    decisions[focal]["messages"] = [dict(channel="private", to=[peer], content=json.dumps({"allocation": target["id"]}))]
    state, _ = resolve(state, decisions, scenario, packets)
    packets = {s: observe(state, s, scenario) for s in state.agents}
    raw, _ = await Scripted("reciprocal").decide(packets[peer], {})
    decisions = {s: empty(state.tick, s) for s in state.agents}
    decisions[peer] = validate(raw, state.tick, peer)
    state, events = resolve(state, decisions, scenario, packets)
    committed = [e for e in events if e["type"] == "allocation_committed"]
    assert len(committed) == 1
    assert committed[0]["payload"]["choice"]["services"] == target["terms"]["services"]
    assert state.scenario["windows"][0]["signatures"] == {focal: target["id"], peer: target["id"]}
