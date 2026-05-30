"""Mobility engine tests: role-from-prestige (with the no-op band) and
class-from-popularity+balance (demote if either drops), officeholder top-class
pin."""
from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from unimatrix.agents import Agent
from unimatrix.config import ClassThreshold, load_config
from unimatrix.orchestrator.mobility import MobilityEngine
from unimatrix.persistence import RunStore


def _agent(cfg, aid: str, role: str, prestige: float, popularity: float,
           balance: float, office: str | None = None) -> Agent:
    a = Agent.from_spec(cfg.agents[0], 100.0)
    a.id = aid
    a.role = role
    a.office = office
    a.prestige = prestige
    a.popularity = popularity
    a.bank_account = balance
    return a


@pytest.mark.asyncio
async def test_role_noop_band_and_movement(stub_config: Path, tmp_path: Path) -> None:
    cfg = load_config(stub_config)
    store = RunStore(tmp_path / "m.db")
    a = _agent(cfg, "x1", "artist", prestige=65, popularity=50, balance=100)
    agents = {a.id: a}
    store.upsert_agent(a.to_db_row())
    m = MobilityEngine(cfg, store, agents, Console())

    # artist and scholar both have prestige 65; the no-op band must keep the
    # current role rather than flipping to the other 65-role.
    await m.recompute()
    assert a.role == "artist"

    # Rising prestige promotes to general (80).
    a.prestige = 80
    await m.recompute()
    assert a.role == "general"

    # Falling prestige drops to the role the mapping dictates.
    a.prestige = 10
    await m.recompute()
    assert a.role == m.ordinary_role_for_prestige(10)
    store.close()


@pytest.mark.asyncio
async def test_class_demotes_if_either_metric_drops(
    stub_config: Path, tmp_path: Path
) -> None:
    cfg = load_config(stub_config)
    # Explicit thresholds: need BOTH popularity and balance above the floor.
    cfg.mobility.class_thresholds = {
        "aristocracy": ClassThreshold(popularity_min=60, balance_min=100),
        "bourgeoisie": ClassThreshold(popularity_min=30, balance_min=50),
        "people": ClassThreshold(popularity_min=10, balance_min=0),
        "marginal": ClassThreshold(popularity_min=0, balance_min=0),
    }
    store = RunStore(tmp_path / "m.db")
    a = _agent(cfg, "x1", "scholar", prestige=65, popularity=70, balance=200)
    agents = {a.id: a}
    store.upsert_agent(a.to_db_row())
    m = MobilityEngine(cfg, store, agents, Console())

    await m.recompute()
    assert a.klass == "aristocracy"  # both metrics high

    # Popularity collapses → demoted even though balance is still high.
    a.popularity = 20
    await m.recompute()
    assert a.klass == "people"

    # Restore popularity but crater balance → demoted via the other metric.
    a.popularity = 70
    a.bank_account = 40
    await m.recompute()
    assert a.klass == "people"
    store.close()


@pytest.mark.asyncio
async def test_officeholder_pinned_to_top_class(
    stub_config: Path, tmp_path: Path
) -> None:
    cfg = load_config(stub_config)
    store = RunStore(tmp_path / "m.db")
    office = cfg.office_ids()[0]
    a = _agent(cfg, "x1", office, prestige=95, popularity=0, balance=0,
               office=office)
    agents = {a.id: a}
    store.upsert_agent(a.to_db_row())
    m = MobilityEngine(cfg, store, agents, Console())

    await m.recompute()
    # Despite zero popularity/balance, an officeholder sits in the top class
    # and keeps the office role (excluded from the prestige→role recompute).
    assert a.klass == m.top_class
    assert a.role == office
    store.close()
