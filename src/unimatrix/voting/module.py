"""Voting module — proposals, mandatory votes, tally, application.

Behavior per spec section 10:
  - Only one active proposal at a time. Concurrent proposals are rejected.
  - Opening a vote interrupts every active conversation.
  - Voting is mandatory. Malformed responses are retried, then recorded as null.
  - Inference is batched: all 50 votes go in a single asyncio.gather.
  - Motivation is public.
  - Simple majority; ties → rejected.
  - On approval the role/class change is immediate; status_change is logged
    and broadcast as a public_event.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from rich.console import Console

from ..agents import Agent, AgentState, PromptBuilder
from ..config import Config
from ..inference import GenerationRequest, InferenceClient
from ..memory import MemoryManager
from ..persistence import RunStore


@dataclass
class Proposal:
    id: int
    proposer_id: str
    target_id: str
    change_type: str  # 'role' | 'class'
    from_value: str
    to_value: str
    motivation: str = ""
    # Pre-vote debate transcript (in-memory only; speeches are also persisted
    # as 'vote_debate_speech' public_events). Each entry has keys:
    # speaker_id, speaker_name, round, text.
    debate_transcript: list[dict] = field(default_factory=list)
    debated: bool = False

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


class VotingModule:
    def __init__(
        self,
        cfg: Config,
        store: RunStore,
        memory: MemoryManager,
        inference: InferenceClient,
        prompts: PromptBuilder,
        agents: dict[str, Agent],
        console: Console | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.memory = memory
        self.inference = inference
        self.prompts = prompts
        self.agents = agents
        self.console = console or Console()
        self.active: Proposal | None = None

    def is_active(self) -> bool:
        return self.active is not None

    async def propose(
        self,
        proposer_id: str,
        target_id: str,
        change_type: str,
        to_value: str,
    ) -> tuple[bool, str]:
        """Validate a proposal. Returns (accepted, reason). Does NOT open it."""
        if change_type not in ("role", "class"):
            return False, "change_type must be 'role' or 'class'"
        target = self.agents.get(target_id)
        if target is None:
            return False, f"unknown target {target_id}"
        if change_type == "role":
            valid = {r.id for r in self.cfg.roles}
            if to_value not in valid:
                return False, f"unknown role {to_value}"
            from_value = target.role
        else:
            if to_value not in self.cfg.classes:
                return False, f"unknown class {to_value}"
            from_value = target.klass
        if from_value == to_value:
            return False, "no-op proposal"
        if self.active is not None:
            return False, "another vote is already in progress"
        return True, ""

    async def open(
        self,
        proposer_id: str,
        target_id: str,
        change_type: str,
        to_value: str,
        motivation: str = "",
    ) -> Proposal | None:
        ok, reason = await self.propose(proposer_id, target_id, change_type, to_value)
        if not ok:
            self.console.log(f"[red]proposal rejected[/]: {reason}")
            return None
        target = self.agents[target_id]
        from_value = target.role if change_type == "role" else target.klass
        prop_id = await asyncio.to_thread(
            self.store.open_proposal,
            proposer_id,
            target_id,
            change_type,
            from_value,
            to_value,
            motivation,
        )
        proposal = Proposal(
            id=prop_id,
            proposer_id=proposer_id,
            target_id=target_id,
            change_type=change_type,
            from_value=from_value,
            to_value=to_value,
            motivation=motivation,
        )
        self.active = proposal
        for a in self.agents.values():
            a.state = AgentState.VOTING
            await asyncio.to_thread(
                self.store.update_agent_state, a.id, state=a.state.value
            )
        await asyncio.to_thread(
            self.store.record_event, "vote_proposed", proposal.to_dict()
        )
        self.console.log(
            f"[bold yellow]VOTE OPENED[/] #{prop_id}: {change_type} for "
            f"{target.name} → {to_value}"
        )
        return proposal

    async def abort_active(self) -> None:
        """Clear a failed active vote and restore non-conversation state."""
        self.active = None
        for a in self.agents.values():
            a.state = AgentState.IDLE
            a.current_conversation_id = None
            await asyncio.to_thread(
                self.store.update_agent_state,
                a.id,
                state=a.state.value,
                current_conversation_id=None,
            )

    async def run_debate(self) -> None:
        """Pre-vote debate: every agent speaks once per round, in parallel.

        Idempotent — guarded by `proposal.debated`. With `debate_rounds=0`
        becomes a no-op that just sets the flag. Each speech is appended to
        the in-memory `proposal.debate_transcript` and persisted as a
        `vote_debate_speech` public_event for post-mortem inspection.
        """
        if self.active is None or self.active.debated:
            return
        rounds = self.cfg.voting.debate_rounds
        if rounds <= 0:
            self.active.debated = True
            return
        proposal = self.active
        target = self.agents[proposal.target_id]
        order: list[Agent] = list(self.agents.values())
        max_tokens = self.cfg.voting.max_tokens_per_debate_speech
        for r in range(rounds):
            reqs: list[GenerationRequest] = []
            for ag in order:
                imp = await self.memory.person_impression(ag.id, target.id)
                medium = await self.memory.medium_term(ag.id)
                msgs, _ = self.prompts.vote_debate_messages(
                    ag,
                    proposal.to_dict() | {"proposer_id": proposal.proposer_id},
                    target,
                    list(proposal.debate_transcript),
                    imp,
                    medium,
                    round_index=r,
                    rounds_total=rounds,
                    max_tokens=max_tokens,
                )
                reqs.append(
                    GenerationRequest(
                        messages=msgs,
                        max_tokens=max_tokens,
                        stub_kind="debate",
                    )
                )
            try:
                raws = await self.inference.generate_batch(reqs)
            except Exception as exc:
                self.console.log(
                    f"[red]debate round {r} failed[/]: {exc}; "
                    "skipping the rest of the debate"
                )
                break
            for ag, raw in zip(order, raws):
                text = (raw or "").strip()
                if not text:
                    continue
                # Take the first non-empty line.
                first_line = next(
                    (ln.strip() for ln in text.splitlines() if ln.strip()),
                    "",
                )
                if not first_line:
                    continue
                entry = {
                    "speaker_id": ag.id,
                    "speaker_name": ag.name,
                    "round": r,
                    "text": first_line,
                }
                proposal.debate_transcript.append(entry)
                await asyncio.to_thread(
                    self.store.record_event,
                    "vote_debate_speech",
                    {"proposal_id": proposal.id, **entry},
                )
        self.active.debated = True
        self.console.log(
            f"[dim]debate closed[/] for proposal #{proposal.id}: "
            f"{len(proposal.debate_transcript)} speeches"
        )

    async def collect_and_close(self) -> dict:
        """Run the batched vote round and apply the outcome.

        Up to `cfg.voting.max_vote_attempts` passes are made over agents whose
        previous response was malformed. Anything still malformed after the
        last attempt is recorded as a 'null' vote that counts toward neither
        yes nor no. The majority is computed only over yes vs. no.
        """
        assert self.active is not None
        proposal = self.active
        target = self.agents[proposal.target_id]

        # Build batched requests for every agent (target included — they vote on themselves).
        order: list[Agent] = list(self.agents.values())
        reqs: list[GenerationRequest] = []
        for ag in order:
            person = await self.memory.person_impression(ag.id, target.id)
            medium = await self.memory.medium_term(ag.id)
            long_term = [
                r.text
                for r in await self.memory.long_term(
                    ag.id, f"{proposal.change_type} change for {target.name}"
                )
            ]
            msgs, _ = self.prompts.vote_messages(
                ag, proposal.to_dict() | {"proposer_id": proposal.proposer_id},
                target, person, medium, long_term,
                debate_transcript=list(proposal.debate_transcript),
            )
            reqs.append(
                GenerationRequest(
                    messages=msgs,
                    max_tokens=4,
                    stub_kind="vote",
                )
            )

        max_attempts = max(1, self.cfg.voting.max_vote_attempts)
        # Per-agent state: index → (final_vote_label, motivation) once decided.
        decided: dict[int, tuple[str, str]] = {}
        # Last raw LLM output we saw per still-pending index, so the UI can
        # show *what* the model actually produced when its vote was malformed.
        last_raw: dict[int, str] = {}
        pending: list[int] = list(range(len(order)))

        for attempt in range(1, max_attempts + 1):
            if not pending:
                break
            retry_reqs = [reqs[i] for i in pending]
            try:
                raws = await self.inference.generate_batch(retry_reqs)
            except Exception as exc:
                self.console.log(
                    f"[red]vote attempt {attempt} failed[/]: {exc}"
                )
                # Don't retry the whole batch on a backend hiccup; treat the
                # remaining as null so the vote still closes.
                break
            still_pending: list[int] = []
            for i, raw in zip(pending, raws):
                last_raw[i] = raw or ""
                tokens = (raw or "").strip().lower().split()
                first = tokens[0].strip(".,!?\"'`*:;)(") if tokens else ""
                if first in ("yes", "no"):
                    decided[i] = (first, "")
                else:
                    still_pending.append(i)
            if still_pending and attempt < max_attempts:
                self.console.log(
                    f"[yellow]vote attempt {attempt}[/]: "
                    f"{len(still_pending)}/{len(pending)} malformed, retrying"
                )
            pending = still_pending

        # Anything still pending after all attempts → null vote.
        for i in pending:
            decided[i] = ("null", "malformed response after all retries")

        # Persist votes and tally.
        yes = no = null = 0
        for i, ag in enumerate(order):
            v, motivation = decided.get(i, ("null", "no response"))
            raw_for_storage = last_raw.get(i) if v == "null" else None
            await asyncio.to_thread(
                self.store.record_vote,
                proposal.id,
                ag.id,
                v,
                motivation,
                raw_for_storage,
            )
            if v == "yes":
                yes += 1
            elif v == "no":
                no += 1
            else:
                null += 1

        # Tally over valid votes only; ties → rejected (existing rule).
        outcome = "approved" if yes > no else "rejected"
        await asyncio.to_thread(
            self.store.close_proposal, proposal.id, outcome, yes, no
        )
        await asyncio.to_thread(
            self.store.record_event,
            "vote_closed",
            {
                "proposal_id": proposal.id,
                "outcome": outcome,
                "yes": yes,
                "no": no,
                "null": null,
                "target_id": proposal.target_id,
                "change_type": proposal.change_type,
                "to_value": proposal.to_value,
            },
        )
        self.console.log(
            f"[bold]VOTE CLOSED[/] #{proposal.id}: yes={yes} no={no} "
            f"null={null} → {outcome}"
        )

        if outcome == "approved":
            await self._apply(proposal, target)

        # Reset everyone to idle
        for a in self.agents.values():
            a.state = AgentState.IDLE
            a.current_conversation_id = None
            await asyncio.to_thread(
                self.store.update_agent_state,
                a.id,
                state=a.state.value,
                current_conversation_id=None,
            )

        result = {
            "proposal_id": proposal.id,
            "outcome": outcome,
            "yes": yes,
            "no": no,
            "null": null,
        }
        self.active = None
        return result

    async def _apply(self, proposal: Proposal, target: Agent) -> None:
        if proposal.change_type == "role":
            old = target.role
            target.role = proposal.to_value
            await asyncio.to_thread(
                self.store.update_agent_state, target.id, role=target.role
            )
        else:
            old = target.klass
            target.klass = proposal.to_value
            await asyncio.to_thread(
                self.store.update_agent_state, target.id, klass=target.klass
            )
        await asyncio.to_thread(
            self.store.record_status_change,
            target.id,
            proposal.change_type,
            old,
            proposal.to_value,
            proposal.id,
        )
        await asyncio.to_thread(
            self.store.record_event,
            "status_change",
            {
                "agent_id": target.id,
                "change_type": proposal.change_type,
                "from": old,
                "to": proposal.to_value,
                "proposal_id": proposal.id,
            },
        )
