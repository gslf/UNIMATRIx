"""Succession as insurance: bringing a being forth must pay off for the bearer,
not only the future — otherwise self-interested, non-anthropomorphic agents never
reproduce (and they didn't).

Two emergent payoffs, both routed through machinery the world already has:
  - birth forges a naturally strong reciprocal parent<->child tie, so a grown,
    co-located child can be drawn on to keep a dying parent going (pooling);
  - a grown, co-located child working a parent's patch lifts both their takes
    (the co-harvest channel).

Like test_finitude, these build a real orchestrator from the shipped
config/standard.json on the stub backend, so they exercise the real numbers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console

from unimatrix.agents import Agent
from unimatrix.agents.prompts import PromptBuilder
from unimatrix.config import load_config
from unimatrix.inference import InferenceClient
from unimatrix.memory import MemoryManager
from unimatrix.orchestrator import Orchestrator
from unimatrix.persistence import Registry, RunStore

REPO = Path(__file__).resolve().parent.parent
STANDARD = REPO / "config" / "standard.json"


async def _make_orch(tmp_path: Path):
    raw = json.loads(STANDARD.read_text(encoding="utf-8"))
    raw["inference"]["backend"] = "stub"
    raw["memory"]["embedding_model"] = ""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load_config(p)

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    registry = Registry(runs_dir)
    db_path = runs_dir / "succ.db"
    run_id = registry.register("succ", str(db_path), {})
    store = RunStore(db_path)
    memory = MemoryManager(cfg.memory, store, run_id, str(tmp_path / "chroma"))
    inference = InferenceClient(cfg.inference)
    orch = Orchestrator(cfg, store, memory, inference, registry, run_id,
                        run_dir=tmp_path, console=Console())
    await orch.initialize()
    return orch, store, memory, inference


async def _bear(orch, store, bear_tick: int = 5):
    """First two beings bring a third forth at `bear_tick`. Returns (a, b, child)."""
    a, b = list(orch.agents.values())[:2]
    # A bond strong enough to gate succession (directed a -> b, as the engine checks).
    store.upsert_relationship(a.id, b.id, "", 2.0, "seed", 1)
    a.traits["fecundity"] = 1.0
    a.sustenance = 20.0  # comfortably above succession_sustenance_cost (6.0)
    orch._tick_no = bear_tick
    before = sum(1 for ag in orch.agents.values() if ag.alive)
    await orch._do_bear_successor(a, {"partner": b.id})
    after = sum(1 for ag in orch.agents.values() if ag.alive)
    assert after == before + 1, "a successor must be born"
    child = next(ag for ag in orch.agents.values() if ag.parent_ids == [a.id, b.id])
    return a, b, child


@pytest.mark.asyncio
async def test_birth_forges_reciprocal_kin_ties(tmp_path: Path) -> None:
    """Birth must connect parent and child in the relationships table — the bond
    that makes the rest of the payoff possible. Four directed ties, all at
    kin_bond_strength."""
    orch, store, memory, inference = await _make_orch(tmp_path)
    s = orch.cfg.world.kin_bond_strength
    a, b, child = await _bear(orch, store)

    for x, y in ((a.id, child.id), (child.id, a.id),
                 (b.id, child.id), (child.id, b.id)):
        rel = store.get_relationship(x, y)
        assert rel is not None, f"missing kin tie {x}->{y}"
        assert rel["strength"] == pytest.approx(s)

    await inference.aclose()
    await memory.close()
    store.close()


@pytest.mark.asyncio
async def test_grown_colocated_child_rescues_dying_parent(tmp_path: Path) -> None:
    """The core payoff: a dying parent is kept going by a grown child standing
    with it — reproduction is insurance, not only altruism."""
    orch, store, memory, inference = await _make_orch(tmp_path)
    w = orch.cfg.world
    a, b, child = await _bear(orch, store)

    # The child is grown (matured) and stands where it was born — with the parent.
    child.born_tick = 0
    orch._tick_no = w.maturity_age_ticks + 1
    child.sustenance = 10.0
    b.sustenance = 0.0  # isolate the child as the only donor with food
    a.vitality = 5.0    # below pooling_rescue_threshold (10)

    await orch._attempt_pool_rescue(a, ceiling=100.0)
    assert a.vitality > 5.0, "a grown, co-located child must keep the parent going"
    assert child.sustenance < 10.0, "the child spends real food"

    await inference.aclose()
    await memory.close()
    store.close()


@pytest.mark.asyncio
async def test_immature_or_displaced_child_is_not_drained(tmp_path: Path) -> None:
    """Newborns and absent children are protected: the payoff is deferred and
    conditional on the child being both grown and present."""
    orch, store, memory, inference = await _make_orch(tmp_path)
    w = orch.cfg.world
    a, b, child = await _bear(orch, store)
    b.sustenance = 0.0  # only the child could possibly donate

    # (i) Immature: just born, standing with the parent — must NOT be tapped.
    orch._tick_no = 5
    child.born_tick = 5
    child.pos_x, child.pos_y = a.pos_x, a.pos_y
    child.sustenance = 10.0
    a.vitality = 5.0
    await orch._attempt_pool_rescue(a, ceiling=100.0)
    assert child.sustenance == pytest.approx(10.0), "an immature child is protected"
    assert a.vitality == pytest.approx(5.0), "no rescue from a dependent newborn"

    # (ii) Grown but displaced: stands on another patch — must NOT be tapped.
    child.born_tick = 0
    orch._tick_no = w.maturity_age_ticks + 1
    child.pos_x, child.pos_y = a.pos_x + 1, a.pos_y
    child.sustenance = 10.0
    a.vitality = 5.0
    await orch._attempt_pool_rescue(a, ceiling=100.0)
    assert child.sustenance == pytest.approx(10.0), "a child elsewhere is not drained"
    assert a.vitality == pytest.approx(5.0), "no rescue across distance"

    await inference.aclose()
    await memory.close()
    store.close()


@pytest.mark.asyncio
async def test_parent_can_rescue_its_young_child(tmp_path: Path) -> None:
    """The gate is one-directional: it constrains a child donating to a parent,
    NOT a parent supporting its own young child (the reverse stays ungated)."""
    orch, store, memory, inference = await _make_orch(tmp_path)
    orch, store2 = orch, store
    a, b, child = await _bear(orch, store)

    # The child is the one failing; the parent holds food and stands with it.
    orch._tick_no = 5
    child.born_tick = 5  # immature — but it is the recipient here, not the donor
    child.pos_x, child.pos_y = a.pos_x, a.pos_y
    a.sustenance = 10.0
    b.sustenance = 0.0
    child.vitality = 5.0
    await orch._attempt_pool_rescue(child, ceiling=100.0)
    assert child.vitality > 5.0, "a parent may keep its young child going"
    assert a.sustenance < 10.0, "the parent spends real food for the child"

    await inference.aclose()
    await memory.close()
    store.close()


@pytest.mark.asyncio
async def test_grown_kin_coharvest_outyields_strangers(tmp_path: Path) -> None:
    """A grown child working alongside a parent draws the kin bonus on top of the
    ordinary co-harvest bonus; two unrelated workers draw only the ordinary one."""
    orch, store, memory, inference = await _make_orch(tmp_path)
    w = orch.cfg.world
    w.sustenance_max = 1000.0  # lift the cap so we measure raw yield
    a, b, child = await _bear(orch, store)

    # Grow the child.
    child.born_tick = 0
    orch._tick_no = w.maturity_age_ticks + 1

    c, d = list(orch.agents.values())[3:5]  # two beings unrelated to each other
    for ag in (a, child, c, d):
        ag.traits["labor_efficiency"] = 1.0  # pin so the arithmetic is exact
        ag.sustenance = 0.0
        ag.vitality = 100.0

    # Parent + grown child on one rich patch; the two strangers on another.
    a.pos_x, a.pos_y = 0, 0
    child.pos_x, child.pos_y = 0, 0
    c.pos_x, c.pos_y = 1, 1
    d.pos_x, d.pos_y = 1, 1
    orch._patches[(0, 0)]["resource"] = 1000.0
    orch._patches[(1, 1)]["resource"] = 1000.0

    for ag in (a, child, c, d):
        await orch._do_work(ag, {})
    await orch._resolve_harvests()

    eco = w.ecology
    per_head = eco.harvest_base_yield + eco.coharvest_bonus_per_partner  # 2 in a crew
    assert c.sustenance == pytest.approx(per_head)
    assert d.sustenance == pytest.approx(per_head)
    assert a.sustenance == pytest.approx(per_head + w.kin_coharvest_bonus)
    assert child.sustenance == pytest.approx(per_head + w.kin_coharvest_bonus)
    assert a.sustenance > c.sustenance, "kin co-labor must beat strangers' co-labor"

    await inference.aclose()
    await memory.close()
    store.close()


# ----- the decision surface: bearing must be a VISIBLE option when possible -----
# Diagnosis from a real run: strongly bonded pairs (closeness 20-31) lived to the
# lifespan cap and NEVER bore a successor, because the chance was never surfaced —
# it sat buried in an abstract action menu while every tick screamed survival. The
# fix names the opportunity, with the partner, exactly when the being qualifies.

def test_decision_prompt_surfaces_bearing_when_eligible() -> None:
    pb = PromptBuilder(load_config(STANDARD))
    a = Agent(id="a", name="a", gender="")
    a.sustenance = 20.0  # >= succession_sustenance_cost (6.0)
    a.traits["fecundity"] = 1.0
    b = Agent(id="b", name="b", gender="")
    msgs, _ = pb.decide_action_messages(
        a, [a, b], [], [], [], False, [],
        world_state={"tick": 5},
        relationships=[{"subject_id": "b", "strength": 5.0}],
        local_view={"here_ids": ["b"]},
    )
    usr = msgs[1].content
    assert "WITHIN YOUR REACH" in usr, "an eligible being must be told it can bear now"
    assert "closely enough to b" in usr, "the qualifying partner must be named"


def test_decision_prompt_hides_bearing_when_not_eligible() -> None:
    pb = PromptBuilder(load_config(STANDARD))
    b = Agent(id="b", name="b", gender="")

    # (i) no tie strong enough → no offer.
    weak = Agent(id="a", name="a", gender="")
    weak.sustenance = 20.0
    msgs, _ = pb.decide_action_messages(
        weak, [weak, b], [], [], [], False, [],
        world_state={"tick": 5},
        relationships=[{"subject_id": "b", "strength": 0.2}],
        local_view={"here_ids": ["b"]},
    )
    assert "WITHIN YOUR REACH" not in msgs[1].content

    # (ii) strong tie but too little held → no offer.
    poor = Agent(id="a", name="a", gender="")
    poor.sustenance = 1.0
    msgs2, _ = pb.decide_action_messages(
        poor, [poor, b], [], [], [], False, [],
        world_state={"tick": 5},
        relationships=[{"subject_id": "b", "strength": 5.0}],
        local_view={"here_ids": ["b"]},
    )
    assert "WITHIN YOUR REACH" not in msgs2[1].content
