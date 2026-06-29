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
    assert w.sustenance_max == 24.0
    assert w.work_vitality_cost == 1.5
    # Labor is working the land: a solo harvest yields the base; a co-present
    # crew each draws more (the co-harvest bonus), so cooperation pays.
    assert w.ecology.harvest_base_yield == 3.0
    assert w.ecology.coharvest_bonus_per_partner > 0


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
async def test_cooperation_outyields_solo_per_head(tmp_path: Path) -> None:
    """The core interdependence invariant: two beings working one patch together
    each draw MORE than a lone worker would — by arithmetic (the co-harvest
    bonus), not by any rule telling them to cooperate."""
    orch, store, memory, inference = await _make_orch(tmp_path)
    w = orch.cfg.world
    w.sustenance_max = 1000.0  # lift the cap so we measure the raw yield
    orch._tick_no = 1
    a, b, c = list(orch.agents.values())[:3]
    for ag in (a, b, c):
        ag.sustenance = 0.0
        ag.traits["labor_efficiency"] = 1.0  # pin so the arithmetic is exact

    # A works patch (0,0) alone; B and C work patch (1,1) together. Make both
    # patches rich enough that demand is fully met (no scaling-down).
    a.pos_x, a.pos_y = 0, 0
    b.pos_x, b.pos_y = c.pos_x, c.pos_y = 1, 1
    orch._patches[(0, 0)]["resource"] = 1000.0
    orch._patches[(1, 1)]["resource"] = 1000.0

    # Solo harvest → the base yield, no bonus.
    await orch._do_work(a, {})
    await orch._resolve_harvests()
    solo_yield = a.sustenance
    assert solo_yield == pytest.approx(w.ecology.harvest_base_yield)  # 3.0

    # Co-present harvest → each of the two draws the base plus one partner's bonus.
    await orch._do_work(b, {})
    await orch._do_work(c, {})
    await orch._resolve_harvests()
    coop_each = w.ecology.harvest_base_yield + w.ecology.coharvest_bonus_per_partner
    assert b.sustenance == pytest.approx(coop_each)
    assert c.sustenance == pytest.approx(coop_each)
    assert coop_each > solo_yield, "cooperation must beat solo per head"

    await inference.aclose()
    await memory.close()
    store.close()


@pytest.mark.asyncio
async def test_mutual_aid_rescues_the_bound(tmp_path: Path) -> None:
    """A being near death draws on those it is bound to — ties are insurance.
    An isolate with the same low vitality gets no such help."""
    orch, store, memory, inference = await _make_orch(tmp_path)
    w = orch.cfg.world
    orch._tick_no = 5
    a, b, lone = list(orch.agents.values())[:3]

    # A is bound to B (a strong tie); B holds food.
    store.upsert_relationship(a.id, b.id, "close", 2.0, "", 1)
    b.sustenance = 10.0
    a.vitality = 5.0  # below pooling_rescue_threshold (10)

    await orch._attempt_pool_rescue(a, ceiling=100.0)
    assert a.vitality > 5.0, "the bound should be pulled back from the edge"
    assert b.sustenance < 10.0, "the giver spends real food"
    assert orch.metrics["mutual_aid_transfers"] == 1

    # The isolate has no ties: no rescue, vitality unchanged.
    lone.vitality = 5.0
    await orch._attempt_pool_rescue(lone, ceiling=100.0)
    assert lone.vitality == 5.0

    await inference.aclose()
    await memory.close()
    store.close()


@pytest.mark.asyncio
async def test_express_free_channel_feeds_the_commons(tmp_path: Path) -> None:
    """The commons (cultural_artifacts) must fill even in the spatial world, where
    every single action is spent on move/harvest to survive. A being voices via
    the FREE `express` channel, which does NOT consume its one action — so ideas
    reach the shared world ('Meaning & Belief') alongside the survival loop."""
    orch, store, memory, inference = await _make_orch(tmp_path)

    # Drive several full decision ticks. The stub attaches a free `out["express"]`
    # with p≈0.3 per agent per tick; over 8 agents × 8 ticks the commons fills.
    for t in range(1, 9):
        orch._tick_no = t
        await orch._run_decisions()

    arts = store.list_artifacts()
    assert arts, "the free express channel must produce cultural artifacts"
    event_kinds = {e["event_type"] for e in store.recent_events(500)}
    assert "expression" in event_kinds, "expression events must be recorded"

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
    ag.traits["labor_efficiency"] = 1.0
    ag.pos_x, ag.pos_y = 0, 0
    orch._patches[(0, 0)]["resource"] = 1000.0  # plenty to draw on

    # One work action costs work_vitality_cost (charged the moment it acts).
    await orch._do_work(ag, {})
    assert ag.vitality == pytest.approx(100.0 - w.work_vitality_cost)
    await orch._resolve_harvests()
    assert ag.sustenance == pytest.approx(w.ecology.harvest_base_yield)  # one solo draw

    # Keep harvesting with a small storage cap — sustenance clamps, never exceeds.
    w.sustenance_max = 5.0
    for _ in range(6):
        await orch._do_work(ag, {})
        await orch._resolve_harvests()
    assert ag.sustenance == pytest.approx(w.sustenance_max)  # clamped at the cap

    await inference.aclose()
    await memory.close()
    store.close()
