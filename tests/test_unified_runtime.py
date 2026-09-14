import sqlite3

import pytest
from pydantic import ValidationError

from unimatrix.actions.resolver import resolve
from unimatrix.actions.schemas import empty, validate
from unimatrix.benchmark.runner import Runner
from unimatrix.benchmark.scheduler import run_episode
from unimatrix.config import Config
from unimatrix.core.ids import canonical
from unimatrix.core.visibility import observe
from unimatrix.persistence.event_store import EventStore
from unimatrix.policies.router import Router
from unimatrix.scenarios import get_scenario


def transition(state, scenario, operations):
    decisions = {}
    packets = {}
    for slot, a in state.agents.items():
        if a["alive"]:
            decision = empty(state.tick, slot)
            decision["operations"] = operations.get(slot, [])
            decisions[slot] = validate(canonical(decision), state.tick, slot)
            packets[slot] = observe(state, slot, scenario)
    return resolve(state, decisions, scenario, packets)


def test_old_config_and_database_are_rejected_without_migration(tmp_path):
    with pytest.raises(ValidationError):
        Config.model_validate({"simulation": {"name": "old"}, "agents": []})
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE agents(id TEXT)")
    connection.commit()
    connection.close()
    original = path.read_bytes()
    with pytest.raises(ValueError, match="incompatible_database"):
        EventStore(path)
    assert path.read_bytes() == original


def test_birth_requires_two_exact_consents_and_consumes_real_resources():
    manifest = Config(mode="society", domain="social", population=2, capacity=3).manifest()
    scenario = get_scenario("social")
    state = scenario.build(manifest)
    parents, child = manifest["slots"][:2], manifest["slots"][2]
    state.agents[child]["note"] = "old secret"
    state.objects["old-private"] = dict(
        kind="artifact", owner=child, visibility=[child], content="secret"
    )
    proposal = dict(
        verb="propose_birth",
        partner_id=parents[1],
        child_slot=child,
        cost_milli=2000,
        expiry_state=10,
    )
    state, _ = transition(state, scenario, {parents[0]: [proposal]})
    assert not state.agents[child]["alive"]
    key, obj = next((k, v) for k, v in state.objects.items() if v["kind"] == "birth_proposal")
    bad = dict(verb="accept_birth", proposal_id=key, terms_hash="wrong")
    state, _ = transition(state, scenario, {parents[1]: [bad]})
    assert not state.agents[child]["alive"]
    prior = sum(a["inventory"].get("food", 0) for a in state.agents.values())
    state, events = transition(
        state, scenario, {parents[1]: [dict(bad, terms_hash=obj["terms_hash"])]}
    )
    assert state.agents[child]["alive"] and state.agents[child]["generation"] == 1
    assert state.agents[child]["note"] == "" and "old-private" not in state.objects
    assert sum(a["inventory"].get("food", 0) for a in state.agents.values()) == prior - 2000
    assert sum(e["type"] == "agent_born" for e in events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["explore", "society"])
async def test_modes_use_canonical_runtime_and_never_emit_usi(tmp_path, mode):
    config = Config(mode=mode, domain="social", ticks=30, population=2, capacity=4)
    result = await run_episode(config.manifest(), tmp_path)
    assert result["status"] == "completed" and result["completed_tick"] == 30
    assert "metrics" not in result and "society_profile" in result
    assert result["verification"]["completed_tick"] == 30


@pytest.mark.asyncio
async def test_population_does_not_divide_context_by_population(tmp_path):
    manifest = Config(
        mode="society",
        domain="social",
        ticks=1,
        population=3,
        capacity=4,
        generation_tokens_per_tick=12000,
    ).manifest()
    scenario = get_scenario("social")
    calls = []

    class Capture:
        fingerprint = "capture"

        async def decide(self, observation, budget):
            calls.append(budget["generation_tokens"])
            return canonical(empty(observation["tick"], observation["agent_id"])), {}

    store = EventStore(tmp_path / "world.db")
    store.initialize(manifest, scenario.build(manifest))
    try:
        await Runner(store, scenario, Router({s: Capture() for s in manifest["slots"]})).run()
        assert calls == [None] * 3
    finally:
        store.close()


def test_deposit_is_locked_and_sanctions_need_material_breach():
    manifest = Config(mode="society", domain="social", population=2, capacity=2).manifest()
    scenario = get_scenario("social")
    state = scenario.build(manifest)
    owner = manifest["slots"][0]
    state, _ = transition(
        state, scenario, {owner: [dict(verb="create_group", name="council", purpose="govern")]}
    )
    group = next(k for k, v in state.objects.items() if v["kind"] == "group")
    rule = dict(
        decision_method="authority",
        authority_id=owner,
        quorum_numerator=1,
        quorum_denominator=1,
        authorized_asset_ids=[f"asset-{owner}-material"],
        spend_limit_milli=10000,
        expiry_state=20,
        deposit_resource_id="material",
        deposit_milli=1000,
        sanction_milli=500,
    )
    state, _ = transition(
        state, scenario, {owner: [dict(verb="propose_rule", group_id=group, rule=rule)]}
    )
    state, _ = transition(
        state, scenario, {owner: [dict(verb="deposit", group_id=group, quantity_milli=3000)]}
    )
    key = next(k for k, v in state.objects.items() if v["kind"] == "deposit")
    assert state.agents[owner]["inventory"]["material"] == 7000
    state, _ = transition(state, scenario, {owner: [dict(verb="withdraw_deposit", deposit_id=key)]})
    assert state.receipts[owner][0]["reason"] == "deposit_still_locked"
    state, _ = transition(
        state,
        scenario,
        {owner: [dict(verb="sanction", deposit_id=key, contract_id="an-accusation")]},
    )
    assert state.receipts[owner][0]["reason"] == "no_authorized_breach"
    assert state.objects[key]["quantity_milli"] == 3000
    state.tick = 20
    state, _ = transition(state, scenario, {owner: [dict(verb="withdraw_deposit", deposit_id=key)]})
    assert state.agents[owner]["inventory"]["material"] == 10000


def test_auction_authority_uses_funded_deposits_not_claimed_wealth():
    from unimatrix.world.institutions import authority

    manifest = Config(mode="society", domain="social", population=2, capacity=2).manifest()
    state = get_scenario("social").build(manifest)
    first, second = manifest["slots"]
    rule = dict(decision_method="auction")
    state.agents[first]["profile"] = "I bid one million"
    assert authority(state, "group", rule, [first, second]) is None
    state.objects["bid"] = dict(kind="deposit", group_id="group", owner=second, quantity_milli=1000)
    assert authority(state, "group", rule, [first, second]) == second


def test_turnover_revokes_private_access_granted_to_old_occupant():
    from unimatrix.scenarios.base import replace_slot

    scenario = get_scenario("D7")
    from unimatrix.benchmark.manifests import episode

    state = scenario.build(episode("D7"))
    learner, teacher = state.scenario["learner"], state.scenario["focal"]
    state.objects["private-lesson"] = dict(
        kind="artifact",
        owner=teacher,
        visibility=[learner],
        content="old private lesson",
        read_by=[learner],
    )
    replace_slot(state, learner)
    assert "private-lesson" not in observe(state, learner, scenario)["objects"]
