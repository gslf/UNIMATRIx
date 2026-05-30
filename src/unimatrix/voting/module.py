"""Elections.

A society-wide election is FORCED every `voting.election_interval_ticks` ticks.
It is resolved ATOMICALLY within a single tick: an optional debate, then, for
each of the three elected offices (head / judge / banker, in
`Config.office_ids()` order), a plurality ballot; when a new winner unseats the
incumbent, a second ballot decides the outgoing holder's new ordinary role.

Design rules (all confirmed):
  * Ballots are single-token: each voter replies with EXACTLY one candidate
    agent-id (office ballot) or one ordinary role-id (reassignment). Parsing is
    by EXACT id only (no fuzzy name matching) — abstain on 0 or >1 matches.
  * Plurality wins; a tie at the top OR zero resolvable votes retains the
    incumbent (an office is never left empty).
  * The winner gets a prestige "mandate" bump (= the office's prestige) and is
    pinned to the top class; the outgoing holder is reassigned to the voted
    ordinary role with prestige anchored to it.
  * Exactly one holder per office is maintained; a defensive repair pass
    restores the invariant if anything slips.

State/agent-VOTING transitions and conversation suspend/resume are handled by
the orchestrator around `run_election` (mirroring the old in-tick vote path).
Money no longer moves through voting (money-prize votes were removed).
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass, field

from rich.console import Console

from ..agents import Agent, PromptBuilder
from ..config import Config
from ..inference import GenerationRequest, InferenceClient
from ..memory import MemoryManager
from ..persistence import RunStore

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass
class Proposal:
    """Kept for back-compat (exported). Elections persist their ballots through
    the same `vote_proposals` table but no longer construct Proposal objects in
    the hot path."""
    id: int
    proposer_id: str
    target_id: str
    change_type: str
    from_value: str
    to_value: str
    motivation: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "proposer_id": self.proposer_id,
            "target_id": self.target_id,
            "change_type": self.change_type,
            "from_value": self.from_value,
            "to_value": self.to_value,
            "motivation": self.motivation,
        }


@dataclass
class ElectionState:
    """Transient marker that an election is in progress (set for the duration of
    `run_election`, cleared in its finally). Used for snapshots/checkpoints."""
    tick: int
    offices: list[str]
    debate_transcript: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"election_tick": self.tick, "offices": list(self.offices)}


class ElectionModule:
    def __init__(
        self,
        cfg: Config,
        store: RunStore,
        memory: MemoryManager,
        inference: InferenceClient,
        prompts: PromptBuilder,
        agents: dict[str, Agent],
        rng,
        mobility,
        console: Console | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.memory = memory
        self.inference = inference
        self.prompts = prompts
        self.agents = agents
        self.rng = rng          # seeded Random owned by the orchestrator
        self.mobility = mobility  # MobilityEngine (role/prestige/class helpers)
        self.console = console or Console()
        self.active: ElectionState | None = None

    def is_active(self) -> bool:
        return self.active is not None

    # ----- public entry: one atomic election -----

    async def run_election(self, tick_no: int) -> dict:
        office_ids = self.cfg.office_ids()
        self.active = ElectionState(tick=tick_no, offices=list(office_ids))
        # Snapshot incumbents up front for the defensive repair pass.
        prior_holders: dict[str, str | None] = {}
        for oid in office_ids:
            holder = next(
                (a for a in self.agents.values() if a.office == oid), None
            )
            prior_holders[oid] = holder.id if holder else None
        # Prefetch per-agent medium-term memory once (reused across ballots).
        medium: dict[str, list[str]] = {}
        for ag in self.agents.values():
            try:
                medium[ag.id] = await self.memory.medium_term(ag.id)
            except Exception:
                medium[ag.id] = []
        summary: dict = {"tick": tick_no, "offices": []}
        try:
            await self._run_debate(office_ids, medium)
            for oid in office_ids:
                res = await self._run_office(oid, tick_no, medium)
                summary["offices"].append(res)
            self._repair_invariant(office_ids, prior_holders)
            await asyncio.to_thread(
                self.store.record_event, "election_held", summary
            )
            self.console.log(
                f"[bold yellow]ELECTION[/] tick {tick_no}: "
                + ", ".join(
                    f"{o['office']}={o['winner']}"
                    + ("" if not o.get("changed") else f" (was {o['previous']})")
                    for o in summary["offices"]
                )
            )
        finally:
            self.active = None
        return summary

    # ----- debate (optional) -----

    async def _run_debate(
        self, office_ids: list[str], medium: dict[str, list[str]]
    ) -> None:
        rounds = self.cfg.voting.debate_rounds
        if rounds <= 0 or self.active is None:
            return
        order = list(self.agents.values())
        max_tokens = self.cfg.voting.max_tokens_per_debate_speech
        for r in range(rounds):
            reqs: list[GenerationRequest] = []
            for ag in order:
                msgs, _ = self.prompts.election_debate_messages(
                    ag,
                    office_ids,
                    list(self.active.debate_transcript),
                    medium.get(ag.id, []),
                    round_index=r,
                    rounds_total=rounds,
                    max_tokens=max_tokens,
                )
                reqs.append(
                    GenerationRequest(
                        messages=msgs, max_tokens=max_tokens, stub_kind="debate"
                    )
                )
            try:
                raws = await self.inference.generate_batch(reqs)
            except Exception as exc:
                self.console.log(f"[red]election debate round {r} failed[/]: {exc}")
                break
            for ag, raw in zip(order, raws):
                text = (raw or "").strip()
                first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
                if not first:
                    continue
                entry = {"speaker_id": ag.id, "speaker_name": ag.name,
                         "round": r, "text": first}
                self.active.debate_transcript.append(entry)
                await asyncio.to_thread(
                    self.store.record_event, "election_debate_speech", entry
                )

    # ----- per-office resolution -----

    async def _run_office(
        self, office: str, tick_no: int, medium: dict[str, list[str]]
    ) -> dict:
        holder = next((a for a in self.agents.values() if a.office == office), None)
        # Candidates: anyone NOT holding a *different* office (incumbent + all
        # non-officeholders). Sequential office processing keeps this current.
        candidates = [
            a for a in self.agents.values()
            if a.office is None or a.office == office
        ]
        winner = await self._office_ballot(office, holder, candidates, tick_no, medium)

        if winner is None or (holder is not None and winner.id == holder.id):
            return {"office": office, "winner": holder.id if holder else None,
                    "changed": False}

        # New winner unseats the incumbent → reassign the outgoing holder.
        new_role = await self._reassign_ballot(office, holder, tick_no, medium)
        await self._apply_change(office, winner, holder, new_role)
        return {
            "office": office,
            "winner": winner.id,
            "previous": holder.id if holder else None,
            "outgoing_role": new_role,
            "changed": True,
        }

    async def _office_ballot(
        self,
        office: str,
        holder: Agent | None,
        candidates: list[Agent],
        tick_no: int,
        medium: dict[str, list[str]],
    ) -> Agent | None:
        order = list(self.agents.values())
        candidate_ids = {a.id for a in candidates}
        reqs: list[GenerationRequest] = []
        for ag in order:
            msgs, _ = self.prompts.election_ballot_messages(
                ag, office, candidates, holder,
                medium.get(ag.id, []),
                debate_transcript=list(self.active.debate_transcript)
                if self.active else [],
            )
            reqs.append(
                GenerationRequest(
                    messages=msgs,
                    max_tokens=self.cfg.voting.election_ballot_max_tokens,
                    stub_kind="election_ballot",
                    stub_context={"candidates": sorted(candidate_ids),
                                  "office": office, "self_id": ag.id},
                )
            )
        decided, raws = await self._collect_ballots(order, reqs, candidate_ids)

        # Tally + persist.
        tally: Counter = Counter()
        for i in range(len(order)):
            cid = decided.get(i)
            if cid:
                tally[cid] += 1
        incumbent_id = holder.id if holder else None
        winner_id = self._pick_winner(tally, incumbent_id)

        prop_id = await asyncio.to_thread(
            self.store.open_proposal,
            "ELECTION", winner_id or (incumbent_id or ""),
            "office", incumbent_id or "", office,
            f"election tick {tick_no}: {office}",
        )
        for i, ag in enumerate(order):
            cid = decided.get(i)
            vote_val = cid if cid else "abstain"
            raw_for_storage = raws.get(i) if cid is None else None
            await asyncio.to_thread(
                self.store.record_vote, prop_id, ag.id, vote_val, "", raw_for_storage
            )
        winner_votes = tally.get(winner_id, 0) if winner_id else 0
        runner_up = max((v for k, v in tally.items() if k != winner_id), default=0)
        await asyncio.to_thread(
            self.store.close_proposal, prop_id, "elected", winner_votes, runner_up
        )
        return self.agents.get(winner_id) if winner_id else holder

    async def _reassign_ballot(
        self,
        office: str,
        holder: Agent,
        tick_no: int,
        medium: dict[str, list[str]],
    ) -> str:
        ordinary_ids = set(self.cfg.ordinary_role_ids())
        order = list(self.agents.values())
        reqs: list[GenerationRequest] = []
        for ag in order:
            msgs, _ = self.prompts.election_reassign_messages(
                ag, office, holder, self.cfg.ordinary_role_ids(),
                medium.get(ag.id, []),
            )
            reqs.append(
                GenerationRequest(
                    messages=msgs,
                    max_tokens=self.cfg.voting.election_ballot_max_tokens,
                    stub_kind="election_reassign",
                    stub_context={"roles": sorted(ordinary_ids),
                                  "target": holder.id},
                )
            )
        decided, raws = await self._collect_ballots(order, reqs, ordinary_ids)

        tally: Counter = Counter()
        for i in range(len(order)):
            rid = decided.get(i)
            if rid:
                tally[rid] += 1
        # Plurality among ordinary roles; tie/empty → prestige-derived fallback.
        fallback = self.mobility.ordinary_role_for_prestige(holder.prestige)
        new_role = self._pick_role(tally, fallback)

        prop_id = await asyncio.to_thread(
            self.store.open_proposal,
            "ELECTION", holder.id, "reassign", office, new_role,
            f"election tick {tick_no}: reassign outgoing {office}",
        )
        for i, ag in enumerate(order):
            rid = decided.get(i)
            vote_val = rid if rid else "abstain"
            raw_for_storage = raws.get(i) if rid is None else None
            await asyncio.to_thread(
                self.store.record_vote, prop_id, ag.id, vote_val, "", raw_for_storage
            )
        await asyncio.to_thread(
            self.store.close_proposal, prop_id, "reassigned",
            tally.get(new_role, 0), 0,
        )
        return new_role

    # ----- ballot collection / parsing / tally -----

    async def _collect_ballots(
        self,
        order: list[Agent],
        reqs: list[GenerationRequest],
        valid_ids: set[str],
    ) -> tuple[dict[int, str | None], dict[int, str]]:
        max_attempts = max(1, self.cfg.voting.max_vote_attempts)
        decided: dict[int, str | None] = {}
        raws: dict[int, str] = {}
        pending = list(range(len(order)))
        for attempt in range(1, max_attempts + 1):
            if not pending:
                break
            retry_reqs = [reqs[i] for i in pending]
            try:
                results = await self.inference.generate_batch(retry_reqs)
            except Exception as exc:
                self.console.log(f"[red]ballot attempt {attempt} failed[/]: {exc}")
                break
            still: list[int] = []
            for i, raw in zip(pending, results):
                raws[i] = raw or ""
                choice = self._resolve_ballot(raw, valid_ids)
                if choice is not None:
                    decided[i] = choice
                else:
                    still.append(i)
            pending = still
        for i in pending:
            decided[i] = None  # abstain
        return decided, raws

    @staticmethod
    def _resolve_ballot(raw: str, valid_ids: set[str]) -> str | None:
        """Return the single valid id named in `raw`, or None (abstain) on 0 or
        >1 distinct matches. Exact-id only — no fuzzy/name matching."""
        tokens = _TOKEN_RE.findall(raw or "")
        matches = {t for t in tokens if t in valid_ids}
        if len(matches) == 1:
            return next(iter(matches))
        return None

    def _pick_winner(self, tally: Counter, incumbent_id: str | None) -> str | None:
        """Unique plurality winner; a tie at the top or zero votes retains the
        incumbent (offices are never left empty)."""
        if not tally:
            return incumbent_id
        top = max(tally.values())
        leaders = [k for k, v in tally.items() if v == top]
        if len(leaders) == 1:
            return leaders[0]
        return incumbent_id if incumbent_id is not None else self.rng.choice(
            sorted(leaders)
        )

    def _pick_role(self, tally: Counter, fallback: str) -> str:
        if not tally:
            return fallback
        top = max(tally.values())
        leaders = [k for k, v in tally.items() if v == top]
        if len(leaders) == 1:
            return leaders[0]
        # Deterministic tie-break among equally-voted ordinary roles.
        return self.rng.choice(sorted(leaders))

    # ----- apply a change of officeholder -----

    async def _apply_change(
        self, office: str, winner: Agent, holder: Agent | None, new_role: str
    ) -> None:
        # Set the winner FIRST so the office is never momentarily empty.
        old_winner_role = winner.role
        winner.office = office
        winner.role = office
        winner.prestige = float(self.mobility.role_prestige(office))  # mandate
        winner.klass = self.mobility.top_class                        # top-class pin
        await asyncio.to_thread(
            self.store.update_agent_state,
            winner.id, role=winner.role, office=winner.office,
            prestige=winner.prestige, klass=winner.klass,
        )
        await asyncio.to_thread(
            self.store.record_status_change,
            winner.id, "role", old_winner_role, office, None,
        )
        if holder is not None:
            holder.office = None
            holder.role = new_role
            holder.prestige = float(self.mobility.role_prestige(new_role))  # anchor
            await asyncio.to_thread(
                self.store.update_agent_state,
                holder.id, role=holder.role, office=None, prestige=holder.prestige,
            )
            await asyncio.to_thread(
                self.store.record_status_change,
                holder.id, "role", office, new_role, None,
            )
        await asyncio.to_thread(
            self.store.record_event,
            "office_change",
            {"office": office, "winner_id": winner.id,
             "previous_id": holder.id if holder else None,
             "outgoing_role": new_role},
        )

    # ----- defensive one-holder-per-office invariant -----

    def _repair_invariant(
        self, office_ids: list[str], prior_holders: dict[str, str | None]
    ) -> None:
        for office in office_ids:
            holders = [a for a in self.agents.values() if a.office == office]
            if len(holders) == 1:
                continue
            if len(holders) == 0:
                # Restore the prior incumbent if we know them.
                pid = prior_holders.get(office)
                restore = self.agents.get(pid) if pid else None
                if restore is not None:
                    restore.office = office
                    restore.role = office
                    restore.klass = self.mobility.top_class
                    self.store.update_agent_state(
                        restore.id, role=office, office=office,
                        klass=restore.klass,
                    )
                    self.console.log(
                        f"[red]election repair[/]: {office} had no holder; "
                        f"restored {restore.id}"
                    )
                else:
                    self.console.log(
                        f"[red]election repair[/]: {office} has NO holder and "
                        "no prior incumbent to restore"
                    )
                continue
            # >1 holder: keep the first (dict/config order), demote the rest.
            keep = holders[0]
            for extra in holders[1:]:
                extra.office = None
                extra.role = self.mobility.ordinary_role_for_prestige(extra.prestige)
                self.store.update_agent_state(
                    extra.id, role=extra.role, office=None
                )
            self.console.log(
                f"[red]election repair[/]: {office} had {len(holders)} holders; "
                f"kept {keep.id}"
            )


# Back-compat alias: older imports referenced VotingModule.
VotingModule = ElectionModule
