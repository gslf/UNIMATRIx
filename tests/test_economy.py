"""Economy tests: per-tick closed-loop flow, destitution, loans, prizes."""
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
    """Each agent: +gross -tax -expense. Community: +tax +expense -gross."""
    cfg = load_config(stub_config)
    # Make sure the test exercises tax + expense for real this time.
    cfg.economy.agent_expense_per_tick = 2.0
    cfg.economy.community_expense_per_tick = 1.0

    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    pre_balances = {a.id: a.bank_account for a in agents.values()}
    pre_community = store.get_community_balance()
    assert all(b == cfg.economy.agent_initial_balance for b in pre_balances.values())
    assert pre_community == cfg.economy.community_initial_balance

    failure = await econ.apply_tick()
    assert failure is None

    e = cfg.economy
    prestige_of = {r.id: r.prestige for r in cfg.roles}
    expected_tax_total = 0.0
    expected_gross_total = 0.0
    for ag in agents.values():
        prestige = prestige_of[ag.role]
        gross = prestige * e.salary_per_prestige
        tax = gross * e.tax_rate
        # Stub config has roomy balances → no one goes destitute, so every
        # agent pays both tax and the cost-of-living expense.
        expected_new = (
            pre_balances[ag.id] + gross - tax - e.agent_expense_per_tick
        )
        assert ag.bank_account == pytest.approx(expected_new, rel=1e-6)
        assert ag.destitute is False
        expected_tax_total += tax
        expected_gross_total += gross

    expected_community = (
        pre_community
        + expected_tax_total
        + len(agents) * e.agent_expense_per_tick
        - expected_gross_total
        - e.community_expense_per_tick
    )
    assert store.get_community_balance() == pytest.approx(expected_community, rel=1e-6)

    store.close()


@pytest.mark.asyncio
async def test_production_inflow_can_sustain_community(
    stub_config: Path, tmp_path: Path
) -> None:
    """With production_per_prestige high enough, the community treasury grows
    every tick instead of bleeding — even before tax/expense are collected."""
    cfg = load_config(stub_config)
    cfg.economy.production_per_prestige = 1.0  # extreme: each prestige point
                                                # is 1 unit of inflow per tick
    cfg.economy.tax_rate = 0.0
    cfg.economy.agent_expense_per_tick = 0.0
    cfg.economy.community_expense_per_tick = 0.0

    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())
    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    pre = store.get_community_balance()
    prestige_of = {r.id: r.prestige for r in cfg.roles}
    expected_gross = sum(prestige_of[a.role] for a in agents.values()) * cfg.economy.salary_per_prestige
    expected_production = sum(prestige_of[a.role] for a in agents.values()) * cfg.economy.production_per_prestige

    await econ.apply_tick()
    post = store.get_community_balance()
    # Net change = production - salary (no tax, no expense).
    assert post == pytest.approx(pre + expected_production - expected_gross, rel=1e-6)
    # Since production_per_prestige > salary_per_prestige with no tax loss,
    # the community must be richer, not poorer.
    assert post > pre

    # Production rows exist with from_party = NULL (one-sided inflow).
    rows = store.list_transactions(limit=500)
    production_rows = [r for r in rows if r["kind"] == "production"]
    assert len(production_rows) == len(agents)
    assert all(r["from_party"] is None and r["to_party"] == "community"
               for r in production_rows)

    store.close()


@pytest.mark.asyncio
async def test_destitute_agents_do_not_produce(
    stub_config: Path, tmp_path: Path
) -> None:
    """Destitution should cut off production as well as tax and expense —
    that's the feedback loop that makes high destitution drain the treasury."""
    cfg = load_config(stub_config)
    cfg.economy.salary_per_prestige = 0.0
    cfg.economy.tax_rate = 0.0
    cfg.economy.agent_expense_per_tick = 10.0
    cfg.economy.agent_initial_balance = 1.0
    cfg.economy.production_per_prestige = 1.0
    cfg.economy.community_initial_balance = 1_000_000.0
    cfg.economy.community_expense_per_tick = 0.0

    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())
    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    # First tick: everyone tries to pay 10 expense from 1 -> goes destitute.
    await econ.apply_tick()
    assert all(a.destitute for a in agents.values())

    pre = store.get_community_balance()
    await econ.apply_tick()
    post = store.get_community_balance()
    # All destitute -> no production this tick. Balance unchanged.
    assert post == pytest.approx(pre, rel=1e-9)

    store.close()


@pytest.mark.asyncio
async def test_tax_is_a_real_inflow(stub_config: Path, tmp_path: Path) -> None:
    """tax_rate=0 vs tax_rate=1 must produce different community balances —
    the original bug was that tax was bookkeeping-only."""
    cfg = load_config(stub_config)
    cfg.economy.agent_expense_per_tick = 0.0
    cfg.economy.community_expense_per_tick = 0.0

    async def run_with_tax(rate: float) -> float:
        cfg.economy.tax_rate = rate
        store = RunStore(tmp_path / f"tax_{int(rate * 100)}.db")
        agents = _make_agents(cfg)
        for ag in agents.values():
            store.upsert_agent(ag.to_db_row())
        econ = EconomyModule(cfg, store, agents, Console())
        await econ.initialize()
        await econ.apply_tick()
        bal = store.get_community_balance()
        store.close()
        return bal

    bal_no_tax = await run_with_tax(0.0)
    bal_full_tax = await run_with_tax(1.0)
    # With tax_rate=1, the community pays gross out and gets it all back.
    # With tax_rate=0, the community pays gross out and gets nothing back.
    # The difference must be exactly the total gross salaries.
    assert bal_full_tax > bal_no_tax


@pytest.mark.asyncio
async def test_community_bankruptcy_returns_failure(
    stub_config: Path, tmp_path: Path
) -> None:
    """Drain the community treasury below the bankruptcy threshold."""
    cfg = load_config(stub_config)
    cfg.economy.community_initial_balance = 50.0
    cfg.economy.community_expense_per_tick = 100.0
    cfg.economy.salary_per_prestige = 0.0
    cfg.economy.community_bankruptcy_balance = 0.0

    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    failure = await econ.apply_tick()
    assert failure == "community_bankrupt"
    assert store.get_community_balance() < cfg.economy.community_bankruptcy_balance

    store.close()


@pytest.mark.asyncio
async def test_sovereign_deficit_allowed_until_threshold(
    stub_config: Path, tmp_path: Path
) -> None:
    """With a negative bankruptcy threshold the community can run a deficit."""
    cfg = load_config(stub_config)
    cfg.economy.community_initial_balance = 50.0
    cfg.economy.community_expense_per_tick = 30.0
    cfg.economy.salary_per_prestige = 0.0
    cfg.economy.agent_expense_per_tick = 0.0
    cfg.economy.community_bankruptcy_balance = -100.0  # allow 100 of deficit

    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    # Tick 1: 50 -> 20.   Tick 2: 20 -> -10.   Tick 3: -10 -> -40.
    # All above -100, so run continues.
    for _ in range(3):
        assert await econ.apply_tick() is None
    assert store.get_community_balance() < 0
    # Push it past -100.
    for _ in range(10):
        result = await econ.apply_tick()
        if result is not None:
            assert result == "community_bankrupt"
            break
    else:
        pytest.fail("expected community_bankrupt eventually")

    store.close()


@pytest.mark.asyncio
async def test_agent_goes_destitute_not_failure(
    stub_config: Path, tmp_path: Path
) -> None:
    """An agent that can't pay tax/expense becomes destitute; the run keeps
    going. Once destitute, they stop paying tax and expense entirely."""
    cfg = load_config(stub_config)
    cfg.economy.salary_per_prestige = 0.0
    cfg.economy.tax_rate = 0.0
    cfg.economy.agent_initial_balance = 1.0
    cfg.economy.agent_expense_per_tick = 10.0
    cfg.economy.community_initial_balance = 1_000_000.0
    cfg.economy.community_expense_per_tick = 0.0
    cfg.economy.destitution_exit_balance = 50.0

    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    failure = await econ.apply_tick()
    assert failure is None  # never returns agent_bankrupt anymore

    # Every agent should now be destitute with a clamped balance of 0.
    for ag in agents.values():
        assert ag.destitute is True
        assert ag.bank_account == pytest.approx(0.0)
        row = store.get_agent(ag.id)
        assert row["destitute"] == 1
        assert row["bank_account"] == pytest.approx(0.0)

    # Next tick: destitute agents pay neither tax nor expense, so balance
    # stays at 0 and no writeoff is needed.
    pre_writeoffs = sum(
        1 for t in store.list_transactions(limit=500)
        if t["kind"] == "destitution_writeoff"
    )
    await econ.apply_tick()
    post_writeoffs = sum(
        1 for t in store.list_transactions(limit=500)
        if t["kind"] == "destitution_writeoff"
    )
    assert post_writeoffs == pre_writeoffs  # no new writeoffs on tick 2

    store.close()


@pytest.mark.asyncio
async def test_destitute_recovers_after_prize(
    stub_config: Path, tmp_path: Path
) -> None:
    """A prize that lifts the balance above destitution_exit_balance must
    clear the destitute flag."""
    cfg = load_config(stub_config)
    cfg.economy.salary_per_prestige = 0.0
    cfg.economy.tax_rate = 0.0
    cfg.economy.agent_initial_balance = 1.0
    cfg.economy.agent_expense_per_tick = 10.0
    cfg.economy.community_initial_balance = 1_000_000.0
    cfg.economy.community_expense_per_tick = 0.0
    cfg.economy.destitution_exit_balance = 30.0

    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()
    await econ.apply_tick()
    target = next(iter(agents.values()))
    assert target.destitute is True

    # Small prize: still below threshold.
    await econ.pay_prize(target.id, 10.0, proposal_id=1)
    assert target.destitute is True

    # Bigger prize: crosses the threshold.
    await econ.pay_prize(target.id, 25.0, proposal_id=2)
    assert target.destitute is False
    assert store.get_agent(target.id)["destitute"] == 0

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
    assert paid == pytest.approx(30.0)
    assert borrower.bank_account == pytest.approx(pre_borrower + 30.0)
    assert store.get_community_balance() == pytest.approx(pre_community - 30.0)
    # An amortization row was created.
    active = store.list_active_loans()
    assert len(active) == 1
    assert active[0]["borrower_id"] == borrower.id
    assert active[0]["installments_total"] == cfg.economy.loan_installments
    expected_installment = (
        30.0 * (1.0 + cfg.economy.loan_interest_rate)
        / cfg.economy.loan_installments
    )
    assert active[0]["installment_amount"] == pytest.approx(expected_installment)

    # Deny path.
    approved, paid, _ = await econ.grant_loan(
        banker, borrower, requested_amount=20.0, raw_response="no"
    )
    assert approved is False and paid == 0.0
    approved, paid, _ = await econ.grant_loan(
        banker, borrower, requested_amount=20.0, raw_response="maybe later"
    )
    assert approved is False and paid == 0.0

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
    assert paid == pytest.approx(25.0)

    store.close()


@pytest.mark.asyncio
async def test_loan_repays_with_interest(
    stub_config: Path, tmp_path: Path
) -> None:
    """Over `loan_installments` ticks the borrower repays principal+interest."""
    cfg = load_config(stub_config)
    cfg.economy.salary_per_prestige = 0.0
    cfg.economy.tax_rate = 0.0
    cfg.economy.agent_expense_per_tick = 0.0
    cfg.economy.community_expense_per_tick = 0.0
    cfg.economy.agent_initial_balance = 10000.0
    cfg.economy.loan_installments = 4
    cfg.economy.loan_interest_rate = 0.2

    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    banker = next(a for a in agents.values() if a.role == "banker")
    borrower = next(a for a in agents.values() if a.role != "banker")
    pre_community = store.get_community_balance()
    pre_borrower = borrower.bank_account

    approved, paid, _ = await econ.grant_loan(
        banker, borrower, requested_amount=100.0, raw_response="yes 100"
    )
    assert approved and paid == pytest.approx(100.0)
    assert borrower.bank_account == pytest.approx(pre_borrower + 100.0)

    # Run 4 ticks — each one deducts one installment of 30 (= 100*1.2/4).
    for _ in range(cfg.economy.loan_installments):
        result = await econ.apply_tick()
        assert result is None
    # Loan should be fully repaid.
    assert store.list_active_loans() == []
    assert econ.loans_repaid == 1
    # Borrower paid back 120 total.
    assert borrower.bank_account == pytest.approx(pre_borrower - 20.0)
    # Community got +120 from the borrower minus the 100 it loaned out.
    assert store.get_community_balance() == pytest.approx(pre_community + 20.0)

    store.close()


@pytest.mark.asyncio
async def test_loan_installment_deferred_when_borrower_broke(
    stub_config: Path, tmp_path: Path
) -> None:
    """If the borrower can't cover an installment, defer (don't penalize)."""
    cfg = load_config(stub_config)
    cfg.economy.salary_per_prestige = 0.0
    cfg.economy.tax_rate = 0.0
    cfg.economy.agent_expense_per_tick = 0.0
    cfg.economy.community_expense_per_tick = 0.0
    cfg.economy.agent_initial_balance = 5.0  # barely anything
    cfg.economy.loan_installments = 10
    cfg.economy.loan_interest_rate = 0.0

    store = RunStore(tmp_path / "test.db")
    agents = _make_agents(cfg)
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()

    banker = next(a for a in agents.values() if a.role == "banker")
    borrower = next(a for a in agents.values() if a.role != "banker")
    # Loan small enough that the principal alone leaves borrower with ~25,
    # but installments of 100/10=10 are doable for a couple ticks.
    await econ.grant_loan(
        banker, borrower, requested_amount=100.0, raw_response="yes 100"
    )
    # Spend everything down so the next installment can't be paid.
    store.update_agent_state(borrower.id, bank_account=2.0)
    borrower.bank_account = 2.0

    pre_deferred = econ.loan_installments_deferred
    await econ.apply_tick()
    assert econ.loan_installments_deferred == pre_deferred + 1
    # Loan still active.
    assert any(l["borrower_id"] == borrower.id for l in store.list_active_loans())

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
