"""Economy module — bank accounts, salaries, taxes, expenses, loans, prizes.

All financial state lives in SQLite (`agents.bank_account` and
`community_account.balance`); this module is the only writer. The orchestrator
calls `apply_tick` once per tick; voting and the loan-request action call
`pay_prize` and `grant_loan` directly.

Two failure modes stop the run:
  - any agent's balance ≤ 0
  - the community balance ≤ 0
Either is treated as a "society can no longer function" condition.
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
        """Run salary/tax/expense for every agent and the community expense.

        Returns:
            None if everyone (including the community) is solvent.
            A short string reason (e.g. "agent_bankrupt:agent_07" or
            "community_bankrupt") if the run should fail.
        """
        e = self.cfg.economy

        for ag in self.agents.values():
            gross = self._role_prestige.get(ag.role, 0) * e.salary_per_prestige
            tax = gross * e.tax_rate
            net = gross - tax
            # Pay net salary from community to agent. Tax never moves — it's
            # the share of gross the community keeps.
            if net > 0:
                await asyncio.to_thread(
                    self.store.apply_transaction,
                    "salary",
                    "community",
                    ag.id,
                    net,
                    f"net salary (gross={gross:.2f}, tax={tax:.2f})",
                    None,
                )
            if tax > 0:
                # Bookkeeping row only — no balance change (community keeps
                # the tax because it never paid it out).
                await asyncio.to_thread(
                    self.store.apply_transaction,
                    "tax",
                    None,
                    None,
                    tax,
                    f"tax withheld from {ag.id}",
                    None,
                )
            if e.agent_expense_per_tick > 0:
                await asyncio.to_thread(
                    self.store.apply_transaction,
                    "agent_expense",
                    ag.id,
                    None,
                    e.agent_expense_per_tick,
                    "fixed per-tick cost of living",
                    None,
                )

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

        # Refresh agent in-memory balances from the DB (single source of truth).
        for ag in self.agents.values():
            ag.bank_account = await asyncio.to_thread(
                self.store.get_agent_balance, ag.id
            )

        community = await asyncio.to_thread(self.store.get_community_balance)
        if community <= 0:
            return "community_bankrupt"
        for ag in self.agents.values():
            if ag.bank_account <= 0:
                return f"agent_bankrupt:{ag.id}"
        return None

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
        """Decide and (if approved) execute a loan transfer.

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
        borrower.bank_account = await asyncio.to_thread(
            self.store.get_agent_balance, borrower.id
        )
        return True, amount, "approved"

    # ----- voted prizes -----

    async def pay_prize(
        self, target_id: str, amount: float, proposal_id: int
    ) -> tuple[bool, float, str]:
        """Move `amount` from community → target. Clamps to the community's
        actual balance; returns (paid, amount_transferred, reason).
        """
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
        self.prizes_paid += 1
        return True, amount, "paid"
