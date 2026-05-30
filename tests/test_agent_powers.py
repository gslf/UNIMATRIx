"""Agent-power tests: praise/denounce, steal (success + caught), gift,
sabotage (blocks an office's next power use)."""
from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from unimatrix.config import load_config
from unimatrix.inference import InferenceClient
from unimatrix.memory import MemoryManager
from unimatrix.orchestrator import Orchestrator
from unimatrix.persistence import Registry, RunStore


class _FakeRng:
    """Deterministic stand-in for random.Random used to force steal/sabotage
    success or failure."""
    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def choice(self, seq):
        return list(seq)[0]

    def shuffle(self, seq) -> None:
        pass

    def sample(self, pop, k):
        return list(pop)[:k]

    def randint(self, a, b):
        return a


async def _make_orch(stub_config: Path, tmp_path: Path):
    cfg = load_config(stub_config)
    runs = tmp_path / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    registry = Registry(runs)
    db = runs / "p.db"
    run_id = registry.register("p", str(db), {})
    store = RunStore(db)
    memory = MemoryManager(cfg.memory, store, run_id, str(tmp_path / "chroma"))
    inference = InferenceClient(cfg.inference)
    orch = Orchestrator(cfg, store, memory, inference, registry, run_id,
                        run_dir=tmp_path, console=Console())
    await orch.initialize()
    return orch, cfg, store, memory, inference


def _two_non_office(orch):
    pool = [a for a in orch.agents.values() if a.office is None]
    return pool[0], pool[1]


@pytest.mark.asyncio
async def test_praise_and_denounce(stub_config: Path, tmp_path: Path) -> None:
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        actor, target = _two_non_office(orch)
        target.popularity = 50.0
        target.prestige = 50.0
        step = cfg.mobility.influence_step
        await orch._apply_influence(actor, {"targets": [target.id], "aspect": "both"}, "praise")
        assert target.popularity == pytest.approx(50 + step)
        assert target.prestige == pytest.approx(50 + step)
        await orch._apply_influence(actor, {"targets": [target.id], "aspect": "popularity"}, "denounce")
        assert target.popularity == pytest.approx(50)
        # persisted
        assert store.get_agent(target.id)["popularity"] == pytest.approx(50)
    finally:
        await inference.aclose()
        await memory.close()
        store.close()


@pytest.mark.asyncio
async def test_steal_success_and_caught(stub_config: Path, tmp_path: Path) -> None:
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        thief, victim = _two_non_office(orch)
        victim.bank_account = 100.0
        store.update_agent_state(victim.id, bank_account=100.0)
        thief.bank_account = 10.0
        store.update_agent_state(thief.id, bank_account=10.0)
        thief.popularity = 50.0

        # Force success (random() < steal_success_prob).
        orch._rng = _FakeRng(0.0)
        await orch._apply_steal(thief, {"target": victim.id, "amount": 30})
        assert victim.bank_account == pytest.approx(70.0)
        assert thief.bank_account == pytest.approx(40.0)
        assert orch.metrics["steals_succeeded"] == 1

        # Force caught (random() >= prob): no transfer, popularity penalty.
        orch._rng = _FakeRng(1.0)
        pre_v = victim.bank_account
        await orch._apply_steal(thief, {"target": victim.id, "amount": 30})
        assert victim.bank_account == pytest.approx(pre_v)
        assert thief.popularity == pytest.approx(
            50.0 - cfg.agent_powers.steal_caught_popularity_penalty
        )
    finally:
        await inference.aclose()
        await memory.close()
        store.close()


@pytest.mark.asyncio
async def test_gift_transfers(stub_config: Path, tmp_path: Path) -> None:
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        giver, target = _two_non_office(orch)
        giver.bank_account = 100.0
        store.update_agent_state(giver.id, bank_account=100.0)
        target.bank_account = 0.0
        store.update_agent_state(target.id, bank_account=0.0)
        await orch._apply_gift(giver, {"target": target.id, "amount": 40})
        assert giver.bank_account == pytest.approx(60.0)
        assert target.bank_account == pytest.approx(40.0)
        assert orch.metrics["gifts_made"] == 1
    finally:
        await inference.aclose()
        await memory.close()
        store.close()


@pytest.mark.asyncio
async def test_sabotage_blocks_next_office_power(
    stub_config: Path, tmp_path: Path
) -> None:
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        # The head office holds the prestige 'decree' power.
        head_office = cfg.office_ids()[0]
        holder = next(a for a in orch.agents.values() if a.office == head_office)
        saboteur = next(a for a in orch.agents.values() if a.office is None)
        victim = next(
            a for a in orch.agents.values()
            if a.office is None and a.id != saboteur.id
        )
        victim.prestige = 50.0

        # Force sabotage success.
        orch._rng = _FakeRng(0.0)
        await orch._apply_sabotage(saboteur, {"target": holder.id})
        assert holder.power_blocked is True
        assert orch.metrics["sabotages_succeeded"] == 1

        # The holder's next decree is skipped (no prestige change) and the flag
        # clears.
        await orch._apply_office_power(
            holder, {"targets": [victim.id], "direction": "raise"}, "decree"
        )
        assert victim.prestige == pytest.approx(50.0)  # unchanged — blocked
        assert holder.power_blocked is False

        # The decree after that works.
        await orch._apply_office_power(
            holder, {"targets": [victim.id], "direction": "raise"}, "decree"
        )
        assert victim.prestige == pytest.approx(
            50.0 + cfg.office_powers.senator_prestige_power
        )
    finally:
        await inference.aclose()
        await memory.close()
        store.close()
