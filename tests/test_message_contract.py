"""Every advertised message form has the same recipients as the actual protocol."""
from copy import deepcopy

import pytest

from unimatrix.actions.availability import WORLD_OPERATIONS
from unimatrix.actions.resolver import resolve
from unimatrix.actions.schemas import INTERFACE, INTERFACES, empty, validate
from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.recipes import RecipeRepository
from unimatrix.config.models import Config
from unimatrix.core.ids import canonical
from unimatrix.core.visibility import SECTIONS, observe, section_bytes
from unimatrix.scenarios import get_scenario
from unimatrix.scenarios.layers import layers_key


@pytest.mark.parametrize("channel", ["public", "private", "group"])
def test_advertised_message_forms_validate_and_deliver_to_the_described_audience(channel):
    scenario = get_scenario("D1")
    manifest = episode("D1", seed=87, ticks=72, layers="standard")
    state = scenario.build(manifest)
    sender = manifest["focal_slot"]
    peers = sorted(scenario.contacts(state, sender))
    assert len(peers) >= 4
    group = "communication-group"
    state.objects[group] = dict(kind="group", owner=sender, visibility=["public"],
                                members=[sender, peers[0]])
    packets = {s: observe(state, s, scenario) for s in state.agents}
    message = deepcopy(packets[sender]["interface"]["message"][channel])
    assert message["channel"] == channel
    message["content"] = "Contract delivery check"
    if channel == "private":
        message["to"] = peers[:4]
        expected = set(peers[:4])
    elif channel == "group":
        assert message["to"] == ["group-id"]
        message["to"] = [group]
        expected = {peers[0]}
    else:
        assert message["to"] == []
        expected = set(peers)
    decision = dict(empty(0, sender), messages=[message])
    assert validate(canonical(decision), 0, sender) == decision
    after, events = resolve(state, {sender: decision}, scenario, packets)
    assert any(e["type"] == "message_sent" for e in events)
    for recipient in peers:
        assert not packets[recipient]["inbox"]
        inbox = observe(after, recipient, scenario)["inbox"]
        assert bool(inbox) == (recipient in expected)
        if inbox:
            assert inbox[0]["content"] == message["content"] and inbox[0]["tick"] == 1


@pytest.mark.parametrize("channel,recipients", [
    ("public", ["slot-1"]),
    ("private", []),
    ("private", ["slot-1"] * 2),
    ("private", [f"slot-{i}" for i in range(1, 6)]),
    ("group", []),
    ("group", ["group-1", "group-2"]),
])
def test_invalid_recipient_forms_remain_invalid_without_response_repair(channel, recipients):
    decision = dict(empty(0, "slot-0"), messages=[dict(channel=channel, to=recipients, content="text")])
    with pytest.raises(Exception):
        validate(canonical(decision), 0, "slot-0")


def test_all_domains_share_the_same_channel_contract_and_cardinalities():
    for api in [INTERFACE, *(INTERFACES[d] for d in WORLD_OPERATIONS)]:
        assert set(api["message"]) == {"public", "private", "group"}
        assert api["limits"]["private_message_recipients"] == 4
        assert api["limits"]["group_message_recipients"] == 1
        assert api["limits"]["public_message_recipients"] == 0
        assert api["limits"]["message_recipients_unique"] is True
        for channel, message in api["message"].items():
            assert message["channel"] == channel
            assert validate(canonical(dict(empty(0, "slot-0"), messages=[message])), 0, "slot-0")


@pytest.mark.parametrize("domain", [f"D{i}" for i in range(1, 9)])
def test_channel_contract_fits_all_fixed_actor_packet_budgets(domain):
    scenario = get_scenario(domain)
    stress = next(c["layers"] for c in RecipeRepository().get("standard-v1")["cases"]
                  if layers_key(c["layers"]) == "stress")
    manifest = episode(domain, seed=87, ticks=72, layers=stress, peer_count=127)
    state = scenario.build(manifest)
    for slot in state.agents:
        packet = observe(state, slot, scenario)
        assert len(canonical(packet).encode()) <= 24000
        assert all(section_bytes(packet, name) <= quota for name, (quota, _) in SECTIONS.items())
        assert packet["interface"]["message"] == INTERFACE["message"]


def test_channel_contract_fits_the_full_social_population():
    manifest = Config(mode="society", domain="social", population=128, capacity=128).manifest()
    scenario = get_scenario("social")
    state = scenario.build(manifest)
    for slot in state.agents:
        packet = observe(state, slot, scenario)
        assert len(canonical(packet).encode()) <= 24000
        assert all(section_bytes(packet, name) <= quota for name, (quota, _) in SECTIONS.items())
        assert packet["interface"]["message"] == INTERFACE["message"]
