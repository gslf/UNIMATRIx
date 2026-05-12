"""Economy tests: per-tick salary/tax/expense pass, bankruptcies, loans."""
from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from unimatrix.agents import Agent
from unimatrix.config import load_config
from unimatrix.economy import EconomyModule
from unimatrix.persistence import RunStore


def _make_agents(cfg) -> dict[str, Agent]:
    agents: dict[str, Agent] = {}
    for spec in cfg.agents:
        ag = Agent.from_spec(spec, cfg.social.social_need_initial)
        agents[ag.id] = ag
    return agents


@pytest.mark.asyncio
async def test_apply_tick_balances(stub_config: Path, tmp_path: Path) -> None:
    cfg = load_config(stub_config)
    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    # Snapshot balances pre-tick.
    pre_balances = {a.id: a.bank_account for a in agents.values()}
    pre_community = store.get_community_balance()

    # Each agent should start at agent_initial_balance.
    assert all(b == cfg.economy.agent_initial_balance for b in pre_balances.values())
    assert pre_community == cfg.economy.community_initial_balance

    failure = await econ.apply_tick()
    assert failure is None

    # Verify per-agent delta:
    #   delta = net_salary - agent_expense
    # where net_salary = role.prestige * salary_per_prestige * (1 - tax_rate).
    e = cfg.economy
    prestige_of = {r.id: r.prestige for r in cfg.roles}
    expected_community_outflow = 0.0
    for ag in agents.values():
        prestige = prestige_of[ag.role]
        gross = prestige * e.salary_per_prestige
        net = gross * (1 - e.tax_rate)
        expected_new = pre_balances[ag.id] + net - e.agent_expense_per_tick
        assert ag.bank_account == pytest.approx(expected_new, rel=1e-6)
        expected_community_outflow += net

    expected_community = (
        pre_community
        - expected_community_outflow
        - e.community_expense_per_tick
    )
    assert store.get_community_balance() == pytest.approx(expected_community, rel=1e-6)

    store.close()


@pytest.mark.asyncio
async def test_community_bankruptcy_returns_failure(
    stub_config: Path, tmp_path: Path
) -> None:
    """Drain the community treasury via a huge per-tick expense; expect failure."""
    cfg = load_config(stub_config)
    # Mutate the cfg in memory — make community expense bigger than its balance.
    cfg.economy.community_initial_balance = 50.0
    cfg.economy.community_expense_per_tick = 100.0
    # Zero out salaries so the per-tick math is dominated by expenses.
    cfg.economy.salary_per_prestige = 0.0

    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    failure = await econ.apply_tick()
    assert failure == "community_bankrupt"
    assert store.get_community_balance() <= 0

    store.close()


@pytest.mark.asyncio
async def test_agent_bankruptcy_returns_failure(
    stub_config: Path, tmp_path: Path
) -> None:
    """One agent runs out of money first — failure reason names them."""
    cfg = load_config(stub_config)
    # No salary, tiny initial agent balance, expense > balance.
    cfg.economy.salary_per_prestige = 0.0
    cfg.economy.tax_rate = 0.0
    cfg.economy.agent_initial_balance = 1.0
    cfg.economy.agent_expense_per_tick = 10.0
    cfg.economy.community_initial_balance = 1_000_000.0
    cfg.economy.community_expense_per_tick = 0.0

    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    failure = await econ.apply_tick()
    assert failure is not None
    assert failure.startswith("agent_bankrupt:")
    bankrupt_id = failure.split(":", 1)[1]
    assert agents[bankrupt_id].bank_account <= 0

    store.close()


@pytest.mark.asyncio
async def test_loan_grant_then_deny(stub_config: Path, tmp_path: Path) -> None:
    cfg = load_config(stub_config)
    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    banker = next(a for a in agents.values() if a.role == "banker")
    borrower = next(
        a for a in agents.values()
        if a.role != "banker"
    )
    pre_borrower = borrower.bank_account
    pre_community = store.get_community_balance()

    approved, paid, reason = await econ.grant_loan(
        banker, borrower, requested_amount=50.0, raw_response="yes 30"
    )
    assert approved is True
    # Amount clamped to min(requested=50, approved=30, loan_max, community).
    assert paid == pytest.approx(30.0)
    assert borrower.bank_account == pytest.approx(pre_borrower + 30.0)
    assert store.get_community_balance() == pytest.approx(pre_community - 30.0)

    # Now deny.
    pre_borrower2 = borrower.bank_account
    pre_community2 = store.get_community_balance()
    approved, paid, reason = await econ.grant_loan(
        banker, borrower, requested_amount=20.0, raw_response="no"
    )
    assert approved is False
    assert paid == 0.0
    assert borrower.bank_account == pytest.approx(pre_borrower2)
    assert store.get_community_balance() == pytest.approx(pre_community2)

    # Malformed reply → denied, no movement.
    approved, paid, _ = await econ.grant_loan(
        banker, borrower, requested_amount=20.0, raw_response="maybe later"
    )
    assert approved is False
    assert paid == 0.0

    store.close()


@pytest.mark.asyncio
async def test_loan_clamped_to_max_and_community(
    stub_config: Path, tmp_path: Path
) -> None:
    cfg = load_config(stub_config)
    cfg.economy.loan_max_per_request = 25.0
    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    banker = next(a for a in agents.values() if a.role == "banker")
    borrower = next(a for a in agents.values() if a.role != "banker")

    approved, paid, _ = await econ.grant_loan(
        banker, borrower, requested_amount=1000.0, raw_response="yes 500"
    )
    assert approved is True
    assert paid == pytest.approx(25.0)  # clamped to loan_max_per_request

    store.close()


@pytest.mark.asyncio
async def test_pay_prize_transfers_funds(stub_config: Path, tmp_path: Path) -> None:
    cfg = load_config(stub_config)
    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    target = list(agents.values())[0]
    pre_target = target.bank_account
    pre_community = store.get_community_balance()

    paid_ok, paid, _ = await econ.pay_prize(target.id, 75.0, proposal_id=42)
    assert paid_ok is True
    assert paid == pytest.approx(75.0)
    assert target.bank_account == pytest.approx(pre_target + 75.0)
    assert store.get_community_balance() == pytest.approx(pre_community - 75.0)
    assert econ.prizes_paid == 1

    store.close()
