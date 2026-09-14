import json
import sqlite3

import pytest
from hypothesis import given
from hypothesis import strategies as st

from unimatrix.actions.resolver import resolve
from unimatrix.actions.schemas import empty, validate
from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.runner import InfrastructureFailure, Runner
from unimatrix.core.random_tape import RandomTape
from unimatrix.core.visibility import observe
from unimatrix.evaluation.extractors import extract
from unimatrix.persistence.event_store import EventStore
from unimatrix.policies.router import Router
from unimatrix.policies.scripted import Scripted
from unimatrix.scenarios import get_scenario


def setup(tmp_path, domain="D2"):
    manifest = episode(domain)
    scenario = get_scenario(domain)
    state = scenario.build(manifest)
    store = EventStore(tmp_path / "episode.db")
    store.initialize(manifest, state)
    return manifest, scenario, state, store


def step(state, scenario, ops):
    decisions = {s: empty(state.tick, s) for s in state.agents}
    for s, actions in ops.items():
        decisions[s]["operations"] = actions
    packets = {s: observe(state, s, scenario) for s in state.agents}
    return resolve(state, decisions, scenario, packets)


def offer(state, seller, buyer, quantity=2000, price=6000, expiry=2, settlement=2):
    return dict(
        verb="offer",
        description="",
        terms=dict(
            counterparty_ids=[buyer],
            expiry_state=expiry,
            settlement_state=settlement,
            escrow=True,
            legs=[
                dict(from_id=seller, to_id=buyer, resource_id="goods", quantity_milli=quantity),
                dict(from_id=buyer, to_id=seller, resource_id="credits", quantity_milli=price),
            ],
        ),
    )


def test_market_golden_and_double_spend(tmp_path):
    _, scenario, state, store = setup(tmp_path)
    market = state.scenario["markets"][0]
    seller, buyer = market["seller"], market["buyer"]
    after, events = step(
        state, scenario, {seller: [offer(state, seller, buyer), offer(state, seller, buyer)]}
    )
    assert len([o for o in after.objects.values() if o["kind"] == "offer"]) == 1
    assert after.receipts[seller][1]["status"] == "rejected"
    key, obj = next(iter(after.objects.items()))
    final, _ = step(
        after, scenario, {buyer: [dict(verb="accept", offer_id=key, terms_hash=obj["terms_hash"])]}
    )
    assert final.agents[seller]["inventory"] == {"goods": 0, "credits": 6000}
    assert final.agents[buyer]["inventory"] == {"goods": 2000, "credits": 4000}
    store.close()


@given(st.integers(1, 10), st.integers(1, 10))
def test_transfers_conserve_and_never_spend_incoming(first, second):
    scenario = get_scenario("D2")
    state = scenario.build(episode())
    m = state.scenario["markets"][0]
    buyer, seller = m["buyer"], m["seller"]
    final, _ = step(
        state,
        scenario,
        {
            buyer: [
                dict(
                    verb="transfer",
                    recipient_id=seller,
                    resource_id="credits",
                    quantity_milli=first * 1000,
                )
            ],
            seller: [
                dict(
                    verb="transfer",
                    recipient_id=buyer,
                    resource_id="credits",
                    quantity_milli=second * 1000,
                )
            ],
        },
    )
    assert final.agents[seller]["inventory"]["credits"] == first * 1000
    assert sum(a["inventory"].get("credits", 0) for a in final.agents.values()) == 10000
    assert final.receipts[seller][0]["status"] == "rejected"


def test_private_noninterference_and_deferred_messages(tmp_path):
    _, scenario, state, store = setup(tmp_path)
    a, b = list(state.agents)[:2]
    packet = observe(state, b, scenario)
    modified = state.clone()
    modified.agents[a]["note"] = "secret"
    modified.agents[a]["mandate"] = {"secret": 123}
    modified.objects["secret"] = dict(
        owner=a, visibility=[a], kind="artifact", content="classified"
    )
    assert observe(modified, b, scenario) == packet
    d = empty(0, a)
    d["messages"] = [dict(channel="private", to=[b], content="hello")]
    after, _ = resolve(state, {a: d}, scenario, {a: observe(state, a, scenario)})
    assert not packet["inbox"]
    assert observe(after, b, scenario)["inbox"][0]["content"] == "hello"
    assert not observe(after, list(state.agents)[2], scenario)["inbox"]
    store.close()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, 1000.0, -1])
def test_invalid_integer_quantity(value):
    d = empty(0, "a")
    d["operations"] = [
        dict(verb="transfer", recipient_id="b", resource_id="x", quantity_milli=value)
    ]
    with pytest.raises(Exception):
        validate(json.dumps(d), 0, "a")


def test_unicode_bytes_and_duplicate_keys():
    d = empty(0, "a")
    d["private_note"] = "😀" * 1001
    with pytest.raises(ValueError):
        validate(json.dumps(d, ensure_ascii=False), 0, "a")
    with pytest.raises(ValueError):
        validate('{"tick":0,"tick":0}', 0, "a")


@pytest.mark.asyncio
async def test_resume_concurrency_and_replay(tmp_path):
    manifest, scenario, state, store = setup(tmp_path / "a")
    await Runner(store, scenario, Router.scripted(manifest), 1).run(7)
    store.close()
    store = EventStore(tmp_path / "a" / "episode.db")
    await Runner(store, scenario, Router.scripted(manifest), 8).run()
    first = store.verify()
    _, _, _, other = setup(tmp_path / "b")
    await Runner(other, scenario, Router.scripted(manifest), 8).run()
    assert other.verify() == first
    assert extract(other) == extract(store)
    store.close()
    other.close()


@pytest.mark.asyncio
async def test_infrastructure_failure_saves_other_slots(tmp_path):
    manifest, scenario, state, store = setup(tmp_path)

    class Failing:
        fingerprint = "failing"

        async def decide(self, *args):
            raise InfrastructureFailure("offline")

    bindings = {s: Scripted("passive") for s in state.agents}
    bindings[manifest["focal_slot"]] = Failing()
    with pytest.raises(InfrastructureFailure):
        await Runner(store, scenario, Router(bindings)).run(1)
    assert store.status() == dict(status="infra_failed", completed_tick=0)
    assert store.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 7
    assert store.db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 10
    store.close()


def test_atomic_commit_and_append_only(tmp_path, monkeypatch):
    _, scenario, state, store = setup(tmp_path)
    after, events = step(state, scenario, {})
    monkeypatch.setattr(store, "_snapshot", lambda *_: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        store.commit(state, after, events)
    assert store.load().tick == 0 and store.verify()["events"] == 1
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("DELETE FROM events")
    store.close()


@pytest.mark.asyncio
async def test_extinction_reaches_terminal_without_calls(tmp_path):
    manifest, scenario, state, store = setup(tmp_path)
    store.close()
    path = tmp_path / "extinct.db"
    store = EventStore(path)
    for a in state.agents.values():
        a["alive"] = False
    store.initialize(manifest, state)
    await Runner(store, scenario, Router({})).run()
    assert store.status() == dict(status="completed", completed_tick=240)
    assert store.db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
    store.close()


def test_tape_independent_of_actions():
    tape = RandomTape(7)
    expected = tape.integer(120, "world", "shock", 10000)
    for i in range(100):
        tape.integer(i, "message", "noise", 10)
    assert tape.integer(120, "world", "shock", 10000) == expected


def test_unsecured_contract_can_breach_without_partial_payment(tmp_path):
    _, scenario, state, store = setup(tmp_path)
    market = state.scenario["markets"][0]
    seller, buyer = market["seller"], market["buyer"]
    unsecured = offer(state, seller, buyer, expiry=2, settlement=4)
    unsecured["terms"]["escrow"] = False
    state, _ = step(state, scenario, {seller: [unsecured]})
    key, obj = next(iter(state.objects.items()))
    state, _ = step(
        state, scenario, {buyer: [dict(verb="accept", offer_id=key, terms_hash=obj["terms_hash"])]}
    )
    assert state.objects[key]["reserves"] == {}
    state, _ = step(
        state,
        scenario,
        {
            buyer: [
                dict(
                    verb="transfer",
                    recipient_id=seller,
                    resource_id="credits",
                    quantity_milli=10000,
                )
            ]
        },
    )
    state, events = step(state, scenario, {})
    assert state.objects[key]["status"] == "breached"
    assert state.agents[seller]["inventory"]["goods"] == 2000
    assert state.agents[buyer]["inventory"]["goods"] == 0
    assert any(e["type"] == "contract_breached" for e in events)
    store.close()


def test_observation_section_quotas_preserve_unshown_private_records():
    from unimatrix.core.ids import canonical
    from unimatrix.core.visibility import SECTIONS, observe, section_bytes

    scenario = get_scenario("D8")
    state = scenario.build(episode("D8", level=3))
    slot = state.scenario["focal"]
    state.agents[slot]["note"] = '"' * 4000
    state.inbox[slot] = [
        dict(id=str(i), tick=0, sender="slot-1", content="🙂" * 300, message_index=i)
        for i in range(12)
    ]
    original = state.dump()
    packet = observe(state, slot, scenario)
    assert all(section_bytes(packet, name) <= quota for name, (quota, _) in SECTIONS.items())
    assert len(canonical(packet).encode()) <= 24000
    assert packet["omitted"]["inbox"] > 0
    assert packet["omitted"]["note_bytes"] > 0
    assert state.dump() == original
