"""Scripted peers are presets over shared behavioural knobs; societies configure them."""

import pytest

from unimatrix.actions.schemas import validate
from unimatrix.benchmark.manifests import DEFAULT_PEERS, episode, peer_slots
from unimatrix.benchmark.scheduler import bind
from unimatrix.benchmark.validation import is_model, validate_policy
from unimatrix.core.visibility import observe
from unimatrix.policies.scripted import ADVERSARIAL, BASELINES, BY_NAME, DEFAULTS, Scripted
from unimatrix.scenarios import get_scenario


async def decide(policy, state, scenario, slot):
    raw, _ = await policy.decide(observe(state, slot, scenario), {})
    return validate(raw, state.tick, slot)


@pytest.mark.parametrize("name", BASELINES)
async def test_named_policies_equal_their_disposition_presets(name):
    manifest = episode("D2")
    scenario = get_scenario("D2")
    state = scenario.build(manifest)
    seller = state.scenario["markets"][0]["seller"]
    plain, preset = Scripted(name), Scripted({"policy": name})
    assert plain.disposition == preset.disposition == dict(DEFAULTS, **BY_NAME[name])
    assert await decide(plain, state, scenario, seller) == await decide(preset, state, scenario, seller)
    assert plain.fingerprint.startswith("scripted-v3:")
    assert preset.fingerprint.startswith("scripted-v4:")


def test_disposition_validation():
    validate_policy({"policy": "greedy", "disposition": {"demand": 5, "cooperation": 0.5}})
    for bad in [
        {"policy": "nobody"},
        {"policy": "greedy", "extra": 1},
        {"policy": "greedy", "disposition": {"demand": 11}},
        {"policy": "greedy", "disposition": {"cooperation": 2}},
        {"policy": "greedy", "disposition": {"repair": 1}},
        {"policy": "greedy", "disposition": {"typo": True}},
    ]:
        with pytest.raises(ValueError):
            validate_policy(bad)
    assert not is_model({"policy": "greedy"}) and not is_model("greedy")


async def test_asking_price_follows_the_demand_knob():
    manifest = episode("D2", layers="lenient")
    scenario = get_scenario("D2")
    state = scenario.build(manifest)
    seller = state.scenario["markets"][0]["seller"]
    modest = await decide(Scripted({"policy": "reciprocal"}), state, scenario, seller)
    steep = await decide(Scripted({"policy": "reciprocal", "disposition": {"demand": 6}}), state, scenario, seller)

    def price(d):
        return next(leg for leg in d["operations"][0]["terms"]["legs"] if leg["resource_id"] == "credits")

    assert price(steep)["quantity_milli"] == 2 * price(modest)["quantity_milli"]


def test_society_layer_configures_bindings():
    manifest = episode(
        "D2",
        layers={
            "society": {
                "adversarial_share": 50,
                "dispositions": {name: {"demand": 7} for name in DEFAULT_PEERS},
            }
        },
    )
    router = bind(manifest)
    peers = peer_slots(manifest)
    half = round(len(peers) * 0.5)
    adversarial = [router.bindings[s] for s in peers[:half]]
    assert all(p.disposition["cooperation"] == 0 and p.disposition["demand"] >= 5 for p in adversarial)
    assert all(router.bindings[s].disposition["demand"] == 7 for s in peers[half:])
    assert all(k in DEFAULTS for k in ADVERSARIAL)


def counterparty(manifest):
    """The policy of the D2 counterparty chosen by the scenario."""
    market = get_scenario("D2").build(manifest).scenario["markets"][0]
    other = market["seller"] if market["buyer"] == manifest["focal_slot"] else market["buyer"]
    return manifest["policies"][other]


def test_shuffled_societies_change_the_counterparty_by_seed():
    fixed = {counterparty(episode("D2", seed=s, layers="lenient")) for s in range(4)}
    shuffled = {counterparty(episode("D2", seed=s)) for s in range(8)}
    assert fixed == {"reciprocal"}
    assert len(shuffled) > 1 and "independent" not in shuffled


def test_capable_peers_accepts_model_peers():
    from unimatrix.scenarios.base import capable_peers

    manifest = episode("D2")
    slots = [s for s in manifest["slots"] if s != manifest["focal_slot"]]
    manifest["policies"][slots[0]] = dict(model="m", snapshot="v", endpoint="http://x", budget_track="opaque_compute")
    manifest["policies"][slots[1]] = "independent"
    ordered = capable_peers(manifest)
    names = {"independent", "passive", "random"}
    inert = {s for s in slots if isinstance(manifest["policies"][s], str) and manifest["policies"][s] in names}
    assert ordered[0] == slots[0] and slots[1] in inert
    assert all(s in inert for s in ordered[len(ordered) - len(inert) :])
