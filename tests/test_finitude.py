"""Finitude: senescence (a hard lifespan + declining vitality ceiling),
the cost of labor, and the sustenance storage cap.

These tests do NOT use the shared `stub_config` fixture — they build a config
straight from config/standard.json so they exercise the real, shipped numbers
and the real orchestrator decay path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console

from unimatrix.config import WorldConfig, load_config
from unimatrix.inference import InferenceClient
from unimatrix.memory import MemoryManager
from unimatrix.orchestrator import Orchestrator
from unimatrix.persistence import Registry, RunStore

REPO = Path(__file__).resolve().parent.parent
STANDARD = REPO / "config" / "standard.json"


# ----- pure: the senescence ceiling -----

def test_vitality_ceiling_declines_to_zero() -> None:
    w = WorldConfig(vitality_initial=100.0, max_age_ticks=200)
    assert w.vitality_ceiling(0) == pytest.approx(100.0)
    assert w.vitality_ceiling(100) == pytest.approx(50.0)
    assert w.vitality_ceiling(200) == pytest.approx(0.0)
    # never negative past the lifespan
    assert w.vitality_ceiling(300) == pytest.approx(0.0)
    # strictly decreasing while alive
    assert w.vitality_ceiling(10) > w.vitality_ceiling(11)


def test_standard_config_has_finitude_fields() -> None:
    cfg = load_config(STANDARD)
    w = cfg.world
    assert w.max_age_ticks == 220
    assert w.sustenance_max == 15.0
    assert w.work_vitality_cost == 1.5
    assert w.project_default_target == 10.0
    assert w.project_sustenance_yield == 24.0


# ----- functional: real orchestrator decay path -----

async def _make_orch(tmp_path: Path) -> tuple[Orchestrator, RunStore, object, object]:
    raw = json.loads(STANDARD.read_text(encoding="utf-8"))
    raw["inference"]["backend"] = "stub"
    raw["memory"]["embedding_model"] = ""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load_config(p)

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    registry = Registry(runs_dir)
    db_path = runs_dir / "fin.db"
    run_id = registry.register("fin", str(db_path), {})
    store = RunStore(db_path)
    memory = MemoryManager(cfg.memory, store, run_id, str(tmp_path / "chroma"))
    inference = InferenceClient(cfg.inference)
    orch = Orchestrator(cfg, store, memory, inference, registry, run_id,
                        run_dir=tmp_path, console=Console())
    await orch.initialize()
    return orch, store, memory, inference


@pytest.mark.asyncio
async def test_senescence_caps_vitality_and_kills_by_age(tmp_path: Path) -> None:
    orch, store, memory, inference = await _make_orch(tmp_path)
    orch.cfg.world.max_age_ticks = 3  # collapse the lifespan for the test

    ag = next(iter(orch.agents.values()))
    ag.vitality = 100.0  # full, well-fed

    # Tick 1: age 1 → ceiling = 100*(1-1/3) ≈ 66.7. Even at full vitality the
    # ceiling must bite (well below the decayed 98).
    orch._tick_no = 1
    await orch._decay_and_resolve_deaths()
    assert ag.alive
    assert ag.vitality <= orch.cfg.world.vitality_ceiling(1) + 1e-6

    # Tick 3: age 3 >= max_age_ticks → forced death by age, however healthy.
    orch._tick_no = 2
    await orch._decay_and_resolve_deaths()
    orch._tick_no = 3
    await orch._decay_and_resolve_deaths()
    assert not ag.alive

    causes = {d["cause"] for d in store.list_deaths()}
    assert "age" in causes

    await inference.aclose()
    await memory.close()
    store.close()


@pytest.mark.asyncio
async def test_work_costs_vitality_and_sustenance_is_capped(tmp_path: Path) -> None:
    orch, store, memory, inference = await _make_orch(tmp_path)
    w = orch.cfg.world
    orch._tick_no = 1

    ag = next(iter(orch.agents.values()))
    ag.vitality = 100.0
    ag.sustenance = 0.0

    # One work action costs work_vitality_cost.
    await orch._do_work(ag, {"project": 0, "goal": "build"})
    assert ag.vitality == pytest.approx(100.0 - w.work_vitality_cost)

    # Drive the same project to completion (target effort, 1 per work). The lone
    # contributor would earn the full yield (24) but the storage cap clamps it.
    pid = store.active_projects()[0]["id"]
    for _ in range(int(w.project_default_target)):
        await orch._do_work(ag, {"project": pid})
    assert ag.sustenance == pytest.approx(w.sustenance_max)
    assert ag.sustenance < w.project_sustenance_yield  # cap actually bit

    await inference.aclose()
    await memory.close()
    store.close()
