"""Economy module — bank accounts, salaries, taxes, expenses, loans, prizes.

All financial state lives in SQLite (`agents.bank_account`,
`agents.destitute`, `community_account.balance`, `loans`); this module is the
only writer. The orchestrator calls `apply_tick` once per tick; voting and the
loan-request action call `pay_prize` and `grant_loan` directly.

Per-tick flow (closed loop — every transfer moves real money):

  for each agent:
      community -> agent          : gross salary (= prestige * salary_per_prestige)
      agent     -> community      : income tax  (= gross * tax_rate)  [skipped if destitute]
      agent     -> community      : cost of living (= agent_expense_per_tick) [skipped if destitute]
      (none)    -> community      : production value (= prestige * production_per_prestige)
                                    [skipped if destitute — labor is the missing
                                    side; one-sided source represents value
                                    created by work that the state captures]
  community     -> (sink)         : community_expense_per_tick (genuine outflow)
  for each active loan:
      borrower  -> community      : installment_amount (deferred if borrower can't pay)

After the pass, destitution is recomputed:
  * any agent with balance < 0 has the deficit erased (clamped to 0) and is
    marked destitute.
  * a destitute agent whose balance has climbed back to or above
    `destitution_exit_balance` (via prize, loan, salary surplus, etc.) is
    promoted back to solvent.

The run is terminated only when the COMMUNITY treasury falls below
`community_bankruptcy_balance` (default 0; a negative value permits sovereign
debt). Individual destitution never ends the run — it is a state of poverty
that other agents can address via prizes, charity-flavored loans, or votes.
"""
from __future__ import annotations

import asyncio

from rich.console import Console

from ..agents import Agent
from ..config import Config
from ..persistence import RunStore


class EconomyModule:
    def __init__(
        self,
        cfg: Config,
        store: RunStore,
        agents: dict[str, Agent],
        console: Console | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.agents = agents
        self.console = console or Console()
        self._role_prestige: dict[str, int] = {
            r.id: r.prestige for r in cfg.roles
        }
        self.prizes_paid: int = 0
        self.loans_repaid: int = 0
        self.loan_installments_deferred: int = 0

    # ----- init -----

    async def initialize(self) -> None:
        """Seed the community treasury and every agent's bank account.

        Idempotent. Existing balances (e.g. resumed run) are never overwritten.
        The transactions log records the seeding via an "init" row that is
        bookkeeping-only (no party movement) — the balance itself is set
        directly on insertion so the totals never double-count.
        """
        e = self.cfg.economy
        first_time = await asyncio.to_thread(
            self.store.init_community_account, e.community_initial_balance
        )
        if first_time:
            await asyncio.to_thread(
                self.store.apply_transaction,
                "init",
                None,
                None,
                e.community_initial_balance,
                "initial community balance",
                None,
            )
        for ag in self.agents.values():
            current = await asyncio.to_thread(self.store.get_agent_balance, ag.id)
            if current == 0.0 and ag.bank_account == 0.0:
                ag.bank_account = e.agent_initial_balance
                await asyncio.to_thread(
                    self.store.update_agent_state,
                    ag.id,
                    bank_account=ag.bank_account,
                )
                await asyncio.to_thread(
                    self.store.apply_transaction,
                    "init",
                    None,
                    None,
                    e.agent_initial_balance,
                    f"initial balance for {ag.id}",
                    None,
                )
            else:
                # Resume path: trust the DB.
                ag.bank_account = current

    # ----- per-tick pass -----

    async def apply_tick(self) -> str | None:
        """Run salary/tax/expense + loan amortization for the tick.

        Returns:
            None if the community is still solvent.
            "community_bankrupt" if the treasury fell below the configured
            bankruptcy threshold; the orchestrator ends the run.
        """
        e = self.cfg.economy

        for ag in self.agents.values():
            prestige = self._role_prestige.get(ag.role, 0)
            gross = prestige * e.salary_per_prestige

            # Gross salary: community -> agent (always paid, even to destitute).
            if gross > 0:
                await asyncio.to_thread(
                    self.store.apply_transaction,
                    "salary",
                    "community",
                    ag.id,
                    gross,
                    f"gross salary (prestige={prestige})",
                    None,
                )

            # Solvent agents pay income tax + cost of living back to community,
            # and their labor produces value captured by the state. Destitute
            # agents are exempt from tax/expense (no clawback from $0) and
            # contribute no production (modeled as unemployed/marginalized).
            if not ag.destitute:
                tax = gross * e.tax_rate
                if tax > 0:
                    await asyncio.to_thread(
                        self.store.apply_transaction,
                        "tax",
                        ag.id,
                        "community",
                        tax,
                        f"income tax (rate={e.tax_rate:.2f})",
                        None,
                    )
                if e.agent_expense_per_tick > 0:
                    await asyncio.to_thread(
                        self.store.apply_transaction,
                        "agent_expense",
                        ag.id,
                        "community",
                        e.agent_expense_per_tick,
                        "cost of living (taxes, fees, public services)",
                        None,
                    )
                production = prestige * e.production_per_prestige
                if production > 0:
                    # One-sided inflow: labor creates value the state captures.
                    # No party-balance is debited (the "missing side" is the
                    # agent's expended effort, not money).
                    await asyncio.to_thread(
                        self.store.apply_transaction,
                        "production",
                        None,
                        "community",
                        production,
                        f"labor value from {ag.id} ({ag.role})",
                        None,
                    )

        # Community sink (depreciation, external outflows).
        if e.community_expense_per_tick > 0:
            await asyncio.to_thread(
                self.store.apply_transaction,
                "community_expense",
                "community",
                None,
                e.community_expense_per_tick,
                "community per-tick running cost",
                None,
            )

        # Loan amortization runs after the wage cycle so borrowers have just
        # been paid and can usually meet the installment.
        await self._process_loan_installments()

        # Refresh agent balances + reconcile destitution status.
        for ag in self.agents.values():
            ag.bank_account = await asyncio.to_thread(
                self.store.get_agent_balance, ag.id
            )
        await self._reconcile_destitution()

        community = await asyncio.to_thread(self.store.get_community_balance)
        if community < e.community_bankruptcy_balance:
            return "community_bankrupt"
        return None

    # ----- destitution -----

    async def _reconcile_destitution(self) -> None:
        """Clamp negative balances and flip destitute flags both ways."""
        e = self.cfg.economy
        for ag in self.agents.values():
            if ag.bank_account < 0:
                # The shortfall is forgiven — destitute agents do not carry
                # negative balances. Realistically the unpaid expense is a
                # transfer the community did not actually receive; we record
                # a corrective entry so the books balance.
                shortfall = -ag.bank_account
                await asyncio.to_thread(
                    self.store.apply_transaction,
                    "destitution_writeoff",
                    "community",
                    ag.id,
                    shortfall,
                    f"writeoff: {ag.id} could not cover tax/expense",
                    None,
                )
                ag.bank_account = await asyncio.to_thread(
                    self.store.get_agent_balance, ag.id
                )
                if not ag.destitute:
                    ag.destitute = True
                    await asyncio.to_thread(
                        self.store.update_agent_state,
                        ag.id,
                        destitute=True,
                    )
                    await asyncio.to_thread(
                        self.store.record_event,
                        "destitution_entered",
                        {"agent_id": ag.id, "role": ag.role},
                    )
            elif ag.destitute and ag.bank_account >= e.destitution_exit_balance:
                ag.destitute = False
                await asyncio.to_thread(
                    self.store.update_agent_state,
                    ag.id,
                    destitute=False,
                )
                await asyncio.to_thread(
                    self.store.record_event,
                    "destitution_exited",
                    {"agent_id": ag.id, "balance": ag.bank_account},
                )

    # ----- loans -----

    def parse_loan_reply(self, raw: str) -> tuple[bool, float]:
        """Parse a banker's single-line reply.

        Accepts: 'yes <amount>' (approved), 'no' (denied), or anything
        malformed (treated as denied).
        """
        tokens = (raw or "").strip().lower().split()
        if not tokens:
            return False, 0.0
        first = tokens[0].strip(".,!?\"'`*:;)(")
        if first != "yes":
            return False, 0.0
        for t in tokens[1:]:
            cleaned = t.strip(".,!?\"'`*:;)(_-")
            try:
                amount = float(cleaned)
                if amount > 0:
                    return True, amount
            except ValueError:
                continue
        return False, 0.0

    async def grant_loan(
        self,
        banker: Agent,
        borrower: Agent,
        requested_amount: float,
        raw_response: str,
    ) -> tuple[bool, float, str]:
        """Decide and (if approved) execute a loan transfer + amortization row.

        Returns (approved, amount_transferred, reason). The banker's reply
        decides yes/no; the amount is clamped to:
          min(requested, banker_approved, loan_max_per_request, community_balance)
        """
        e = self.cfg.economy
        approved, amount = self.parse_loan_reply(raw_response)
        if not approved:
            return False, 0.0, "denied by banker"
        amount = min(
            float(amount),
            float(requested_amount),
            float(e.loan_max_per_request),
        )
        community = await asyncio.to_thread(self.store.get_community_balance)
        amount = min(amount, max(0.0, community))
        if amount <= 0:
            return False, 0.0, "community has no funds"
        await asyncio.to_thread(
            self.store.apply_transaction,
            "loan",
            "community",
            borrower.id,
            amount,
            f"loan approved by {banker.id} ({banker.name})",
            None,
        )
        # Amortization schedule: total_owed = principal * (1 + rate), split
        # evenly across `loan_installments` ticks.
        total_owed = amount * (1.0 + e.loan_interest_rate)
        installment = total_owed / max(1, e.loan_installments)
        loan_id = await asyncio.to_thread(
            self.store.create_loan,
            borrower.id,
            banker.id,
            amount,
            e.loan_interest_rate,
            installment,
            e.loan_installments,
        )
        borrower.bank_account = await asyncio.to_thread(
            self.store.get_agent_balance, borrower.id
        )
        # Receiving a loan can lift a destitute borrower back above the
        # recovery threshold; reconcile right away so they pay tax next tick.
        if borrower.destitute and borrower.bank_account >= e.destitution_exit_balance:
            borrower.destitute = False
            await asyncio.to_thread(
                self.store.update_agent_state,
                borrower.id,
                destitute=False,
            )
        await asyncio.to_thread(
            self.store.record_event,
            "loan_granted",
            {
                "loan_id": loan_id,
                "borrower_id": borrower.id,
                "principal": amount,
                "installment": installment,
                "installments_total": e.loan_installments,
            },
        )
        return True, amount, "approved"

    async def _process_loan_installments(self) -> None:
        """Debit one installment from each active loan. If a borrower can't
        afford it without going negative, defer (no penalty for V1)."""
        loans = await asyncio.to_thread(self.store.list_active_loans)
        if not loans:
            return
        for loan in loans:
            borrower_id = loan["borrower_id"]
            installment = float(loan["installment_amount"])
            balance = await asyncio.to_thread(
                self.store.get_agent_balance, borrower_id
            )
            if balance < installment:
                self.loan_installments_deferred += 1
                continue
            await asyncio.to_thread(
                self.store.apply_transaction,
                "loan_repayment",
                borrower_id,
                "community",
                installment,
                f"installment for loan {loan['id']}",
                int(loan["id"]),
            )
            await asyncio.to_thread(
                self.store.record_loan_installment, int(loan["id"])
            )
            if int(loan["installments_paid"]) + 1 >= int(loan["installments_total"]):
                self.loans_repaid += 1
                await asyncio.to_thread(
                    self.store.record_event,
                    "loan_repaid",
                    {"loan_id": int(loan["id"]), "borrower_id": borrower_id},
                )

    # ----- voted prizes -----

    async def pay_prize(
        self, target_id: str, amount: float, proposal_id: int
    ) -> tuple[bool, float, str]:
        """Move `amount` from community → target. Clamps to the community's
        actual balance; returns (paid, amount_transferred, reason). A prize
        can lift the recipient out of destitution if it crosses the recovery
        threshold.
        """
        e = self.cfg.economy
        amount = max(0.0, float(amount))
        if amount <= 0:
            return False, 0.0, "non-positive amount"
        community = await asyncio.to_thread(self.store.get_community_balance)
        amount = min(amount, max(0.0, community))
        if amount <= 0:
            return False, 0.0, "community has no funds"
        await asyncio.to_thread(
            self.store.apply_transaction,
            "prize",
            "community",
            target_id,
            amount,
            f"voted prize (proposal {proposal_id})",
            proposal_id,
        )
        ag = self.agents.get(target_id)
        if ag is not None:
            ag.bank_account = await asyncio.to_thread(
                self.store.get_agent_balance, target_id
            )
            if ag.destitute and ag.bank_account >= e.destitution_exit_balance:
                ag.destitute = False
                await asyncio.to_thread(
                    self.store.update_agent_state,
                    target_id,
                    destitute=False,
                )
        self.prizes_paid += 1
        return True, amount, "paid"

    # ----- agent-to-agent transfers (steal / gift) and office money powers -----

    async def _refresh_and_recover(self, ag: Agent) -> None:
        """Sync an agent's in-memory balance from the DB and clear destitution
        if a received transfer lifted them over the recovery threshold."""
        if ag is None:
            return
        ag.bank_account = await asyncio.to_thread(
            self.store.get_agent_balance, ag.id
        )
        if ag.destitute and ag.bank_account >= self.cfg.economy.destitution_exit_balance:
            ag.destitute = False
            await asyncio.to_thread(
                self.store.update_agent_state, ag.id, destitute=False
            )

    async def agent_transfer(
        self,
        from_agent: Agent,
        to_agent: Agent,
        amount: float,
        kind: str,
        reason: str,
    ) -> float:
        """Move `amount` from one agent to another, clamped to the sender's
        balance so nobody goes negative. Returns the amount actually moved."""
        amount = max(0.0, float(amount))
        sender_balance = await asyncio.to_thread(
            self.store.get_agent_balance, from_agent.id
        )
        amount = min(amount, max(0.0, sender_balance))
        if amount <= 0:
            return 0.0
        await asyncio.to_thread(
            self.store.apply_transaction,
            kind,
            from_agent.id,
            to_agent.id,
            amount,
            reason,
            None,
        )
        from_agent.bank_account = await asyncio.to_thread(
            self.store.get_agent_balance, from_agent.id
        )
        await self._refresh_and_recover(to_agent)
        return amount

    async def steal(
        self, thief: Agent, victim: Agent, amount: float
    ) -> tuple[bool, float, str]:
        """Execute a (already-decided-successful) theft: move coins from victim
        to thief, capped by steal_max and the victim's balance. The risk roll
        (success vs caught) is the caller's responsibility."""
        amount = min(float(amount), float(self.cfg.agent_powers.steal_max))
        moved = await self.agent_transfer(
            victim,  # from = victim
            thief,   # to = thief
            amount,
            "theft",
            f"{thief.id} stole from {victim.id}",
        )
        if moved <= 0:
            return False, 0.0, "victim has no funds"
        return True, moved, "stolen"

    async def gift(
        self, giver: Agent, target: Agent, amount: float
    ) -> tuple[bool, float, str]:
        """Voluntary giver->target transfer, capped by gift_max and balance."""
        amount = min(float(amount), float(self.cfg.agent_powers.gift_max))
        moved = await self.agent_transfer(
            giver, target, amount, "gift", f"{giver.id} gifted {target.id}"
        )
        if moved <= 0:
            return False, 0.0, "no funds to give"
        return True, moved, "gifted"

    async def treasury_to_agent(
        self, target: Agent, amount: float, kind: str, reason: str
    ) -> float:
        """Community -> agent transfer (banker subsidy), clamped to the
        treasury balance. Returns the amount moved."""
        amount = max(0.0, float(amount))
        community = await asyncio.to_thread(self.store.get_community_balance)
        amount = min(amount, max(0.0, community))
        if amount <= 0:
            return 0.0
        await asyncio.to_thread(
            self.store.apply_transaction,
            kind, "community", target.id, amount, reason, None,
        )
        await self._refresh_and_recover(target)
        return amount

    async def agent_to_treasury(
        self, agent: Agent, amount: float, kind: str, reason: str
    ) -> float:
        """Agent -> community transfer (banker levy / judge fine), clamped to
        the agent's balance. Returns the amount moved."""
        amount = max(0.0, float(amount))
        bal = await asyncio.to_thread(self.store.get_agent_balance, agent.id)
        amount = min(amount, max(0.0, bal))
        if amount <= 0:
            return 0.0
        await asyncio.to_thread(
            self.store.apply_transaction,
            kind, agent.id, "community", amount, reason, None,
        )
        agent.bank_account = await asyncio.to_thread(
            self.store.get_agent_balance, agent.id
        )
        return amount
