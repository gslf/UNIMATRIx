"""Election tests (stub backend).

Covers: office reconciliation at init (the stub world starts with two bankers),
atomic single-tick election invariants, all-abstain retaining incumbents,
exact-id ballot parsing, and a forced office change with outgoing reassignment +
mandate bump.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from unimatrix.config import load_config
from unimatrix.inference import InferenceClient
from unimatrix.memory import MemoryManager
from unimatrix.orchestrator import Orchestrator
from unimatrix.persistence import Registry, RunStore


async def _make_orch(stub_config: Path, tmp_path: Path):
    cfg = load_config(stub_config)
    runs = tmp_path / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    registry = Registry(runs)
    db = runs / "e.db"
    run_id = registry.register("e", str(db), {})
    store = RunStore(db)
    memory = MemoryManager(cfg.memory, store, run_id, str(tmp_path / "chroma"))
    inference = InferenceClient(cfg.inference)
    orch = Orchestrator(cfg, store, memory, inference, registry, run_id,
                        run_dir=tmp_path, console=Console())
    await orch.initialize()
    return orch, cfg, store, memory, inference


def _office_holders(orch, office_id: str) -> list:
    return [a for a in orch.agents.values() if a.office == office_id]


@pytest.mark.asyncio
async def test_two_banker_reconciliation(stub_config: Path, tmp_path: Path) -> None:
    """The 6-agent stub world has two 'banker' agents; init must keep exactly
    one in the banker office and demote the other to an ordinary role."""
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        offices = set(cfg.office_ids())
        for oid in cfg.office_ids():
            assert len(_office_holders(orch, oid)) == 1, f"office {oid} not unique"
        # Every officeholder's role mirrors its office and is in the top class.
        for a in orch.agents.values():
            if a.office is not None:
                assert a.role == a.office
                assert a.klass == orch.mobility.top_class
            else:
                assert a.role not in offices, f"{a.id} has office role but no office"
    finally:
        await inference.aclose()
        await memory.close()
        store.close()


@pytest.mark.asyncio
async def test_election_atomic_invariants(stub_config: Path, tmp_path: Path) -> None:
    """After an atomic election: exactly one holder per office, every agent back
    to IDLE, no active election."""
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        await orch._run_election()
        assert not orch.voting.is_active()
        for oid in cfg.office_ids():
            assert len(_office_holders(orch, oid)) == 1
        assert all(a.state.value == "idle" for a in orch.agents.values())
    finally:
        await inference.aclose()
        await memory.close()
        store.close()


@pytest.mark.asyncio
async def test_all_abstain_retains_incumbents(stub_config: Path, tmp_path: Path) -> None:
    """If every ballot is unresolvable (abstain), each incumbent is retained."""
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        before = {oid: _office_holders(orch, oid)[0].id for oid in cfg.office_ids()}

        async def _abstain_batch(reqs):
            return ["" for _ in reqs]  # empty → abstain on every ballot

        inference.generate_batch = _abstain_batch  # type: ignore[assignment]
        await orch.voting.run_election(100)

        after = {oid: _office_holders(orch, oid)[0].id for oid in cfg.office_ids()}
        assert after == before
    finally:
        await inference.aclose()
        await memory.close()
        store.close()


@pytest.mark.asyncio
async def test_forced_office_change_reassigns_and_mandate(
    stub_config: Path, tmp_path: Path
) -> None:
    """Drive every voter to elect a fixed non-incumbent for the first office;
    the outgoing holder is reassigned to the voted ordinary role and the winner
    gets the office, the office role, the top class, and the mandate prestige."""
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        head_office = cfg.office_ids()[0]
        old_head = _office_holders(orch, head_office)[0]
        # A non-officeholder candidate to elect into the head office.
        challenger = next(
            a for a in orch.agents.values() if a.office is None
        )

        async def _scripted_batch(reqs):
            out = []
            for r in reqs:
                if r.stub_kind == "election_ballot":
                    out.append(challenger.id)
                elif r.stub_kind == "election_reassign":
                    out.append("worker")
                else:
                    out.append("a speech")
            return out

        inference.generate_batch = _scripted_batch  # type: ignore[assignment]
        await orch.voting.run_election(100)

        # Challenger now holds the head office.
        assert challenger.office == head_office
        assert challenger.role == head_office
        assert challenger.klass == orch.mobility.top_class
        assert challenger.prestige == float(orch.mobility.role_prestige(head_office))
        assert len(_office_holders(orch, head_office)) == 1
        # Outgoing head reassigned to the voted ordinary role, prestige anchored.
        assert old_head.office is None
        assert old_head.role == "worker"
        assert old_head.prestige == float(orch.mobility.role_prestige("worker"))
    finally:
        await inference.aclose()
        await memory.close()
        store.close()


@pytest.mark.asyncio
async def test_tick1_no_spurious_mobility(stub_config: Path, tmp_path: Path) -> None:
    """With default config, the first mobility recompute after init must not
    move anyone's role or class (initial prestige = role prestige, initial
    popularity = class floor, balances at the starting value)."""
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        before = {a.id: (a.role, a.klass) for a in orch.agents.values()}
        await orch.mobility.recompute()
        after = {a.id: (a.role, a.klass) for a in orch.agents.values()}
        assert after == before
    finally:
        await inference.aclose()
        await memory.close()
        store.close()


@pytest.mark.asyncio
async def test_resume_round_trips_new_fields(stub_config: Path, tmp_path: Path) -> None:
    """prestige / popularity / office survive a pause+resume (DB authoritative)."""
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    db_path = store.path
    cfg_obj = cfg
    registry = orch.registry
    run_id = orch.run_id
    try:
        a = next(x for x in orch.agents.values() if x.office is None)
        a.prestige = 42.0
        a.popularity = 33.0
        store.update_agent_state(a.id, prestige=42.0, popularity=33.0)
        holder_office = cfg.office_ids()[0]
        holder_id = next(x.id for x in orch.agents.values() if x.office == holder_office)
        await orch._write_checkpoint()
    finally:
        await inference.aclose()
        await memory.close()
        store.close()

    # Re-attach on the same DB.
    store2 = RunStore(db_path)
    memory2 = MemoryManager(cfg_obj.memory, store2, run_id, str(tmp_path / "chroma2"))
    inference2 = InferenceClient(cfg_obj.inference)
    orch2 = Orchestrator(cfg_obj, store2, memory2, inference2, registry, run_id,
                         run_dir=tmp_path, console=Console())
    try:
        await orch2.initialize_from_existing()
        assert orch2.agents[a.id].prestige == pytest.approx(42.0)
        assert orch2.agents[a.id].popularity == pytest.approx(33.0)
        assert orch2.agents[holder_id].office == holder_office
        # Exactly one holder per office survived (no re-reconciliation flap).
        for oid in cfg_obj.office_ids():
            assert len(_office_holders(orch2, oid)) == 1
    finally:
        await inference2.aclose()
        await memory2.close()
        store2.close()


@pytest.mark.asyncio
async def test_ballot_parsing(stub_config: Path, tmp_path: Path) -> None:
    """Exact-id parsing: one match wins; zero or ambiguous → abstain; an office
    id is not a valid ordinary-role ballot."""
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        em = orch.voting
        cand = {"agent_03", "agent_04"}
        assert em._resolve_ballot("agent_03", cand) == "agent_03"
        assert em._resolve_ballot("I choose agent_04 firmly", cand) == "agent_04"
        assert em._resolve_ballot("agent_03 or maybe agent_04", cand) is None
        assert em._resolve_ballot("nobody", cand) is None
        assert em._resolve_ballot("", cand) is None
        # Office ids are excluded from the ordinary-role ballot set.
        ordinary = set(cfg.ordinary_role_ids())
        assert em._resolve_ballot(cfg.office_ids()[0], ordinary) is None
        assert em._resolve_ballot("worker", ordinary) == "worker"
    finally:
        await inference.aclose()
        await memory.close()
        store.close()
