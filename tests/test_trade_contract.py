"""Public trade timing matches actual offer acceptance and settlement boundaries."""

import pytest

from tests.test_benchmark_core import offer, step
from unimatrix.benchmark.manifests import episode
from unimatrix.core.visibility import observe
from unimatrix.core.waiting import information_signature
from unimatrix.scenarios import get_scenario


@pytest.mark.parametrize("ticks", [72, 240])
@pytest.mark.parametrize("window", [0, 3, 5])
def test_public_deadline_defines_valid_and_invalid_market_offers(ticks, window):
    scenario = get_scenario("D2")
    state = scenario.build(episode("D2", ticks=ticks, seed=91))
    width = ticks // 6
    state.scenario["window"] = window
    scenario.fund(state)
    market = state.scenario["markets"][window]
    seller, buyer = market["seller"], market["buyer"]

    for tick in [window * width, (window + 1) * width - 2, (window + 1) * width - 1]:
        state.tick = tick
        packet = observe(state, seller, scenario)
        public = packet["scenario"]["trade_contract"]
        deadline = public["deadline_state"]
        assert deadline == packet["scenario"]["due"]
        assert "absolute" in public["offer_timing"]
        assert "tick + 2 <= expiry_state <= settlement_state <= deadline_state" in public["offer_timing"]
        for expiry, settlement in [
            (tick + 2, tick + 2),
            (tick + 2, deadline),
            (deadline, deadline),
            (tick + 1, deadline),
            (deadline, deadline - 1),
            (deadline, deadline + 1),
        ]:
            op = offer(state, seller, buyer, quantity=1000, price=1000,
                       expiry=expiry, settlement=settlement)
            _, events = step(state, scenario, {seller: [op]})
            created = any(e["type"] == "offer_created" for e in events)
            valid = tick + 2 <= expiry <= settlement <= deadline
            assert created is valid
            if not valid:
                assert any(e["type"] == "operation_rejected"
                           and e["payload"]["reason"] == "invalid_deadline" for e in events)


@pytest.mark.parametrize("ticks", [72, 240])
def test_visible_offer_can_be_signed_and_settles_at_the_published_state(ticks):
    scenario = get_scenario("D2")
    state = scenario.build(episode("D2", ticks=ticks, layers="lenient"))
    packet = observe(state, state.scenario["focal"], scenario)
    seller, buyer = packet["scenario"]["seller"], packet["scenario"]["buyer"]
    public = packet["scenario"]["trade_contract"]
    state.tick = public["deadline_state"] - 2
    op = offer(state, seller, buyer, quantity=1000, price=1000,
               expiry=public["deadline_state"], settlement=public["deadline_state"])
    state, _ = step(state, scenario, {seller: [op]})
    buyer_view = observe(state, buyer, scenario)
    ident, obj = next((k, v) for k, v in buyer_view["objects"].items() if v["kind"] == "offer")
    assert state.tick + 1 == obj["terms"]["expiry_state"]
    final, events = step(state, scenario, {buyer: [
        dict(verb="accept", offer_id=ident, terms_hash=obj["terms_hash"])]})
    assert final.tick == public["deadline_state"]
    assert any(e["type"] == "contract_accepted" for e in events)
    transfers = [e for e in events if e["type"] == "transfer_settled"]
    assert len(transfers) == 2

    assert final.scenario["results"][0]["delivered"] == 1


@pytest.mark.parametrize("domain", [f"D{i}" for i in range(1, 9) if i != 2])
def test_other_worlds_publish_their_actual_trade_horizon(domain):
    scenario = get_scenario(domain)
    state = scenario.build(episode(domain, ticks=72, seed=91))
    focal = state.scenario["focal"]
    contract = observe(state, focal, scenario)["scenario"]["trade_contract"]
    assert contract["deadline_state"] == 72 == scenario.deadline(state)
    other = state.clone()
    other.scenario["private_test_secret"] = "not observable"
    assert observe(other, focal, scenario)["scenario"]["trade_contract"] == contract


def test_trade_contract_does_not_wake_waiting_actors_on_every_tick():
    scenario = get_scenario("D2")
    state = scenario.build(episode("D2", ticks=72))
    focal = state.scenario["focal"]
    first = observe(state, focal, scenario)
    state.tick += 1
    second = observe(state, focal, scenario)
    assert first["scenario"]["trade_contract"] == second["scenario"]["trade_contract"]
    assert information_signature(first) == information_signature(second)
