"""Voting module — proposals, mandatory votes, tally, application.

Behavior per spec section 10:
  - Only one active proposal at a time. Concurrent proposals are rejected.
  - Opening a vote interrupts every active conversation.
  - Voting is mandatory. Malformed responses → one retry, else default to 'no'.
  - Inference is batched: all 50 votes go in a single asyncio.gather.
  - Reasoning is public.
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
from ..inference.client import parse_json_lenient
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
        if self.active is not None and self.cfg.voting.one_active_vote_at_a_time:
            return False, "another vote is already in progress"
        return True, ""

    async def open(
        self,
        proposer_id: str,
        target_id: str,
        change_type: str,
        to_value: str,
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
        )
        proposal = Proposal(
            id=prop_id,
            proposer_id=proposer_id,
            target_id=target_id,
            change_type=change_type,
            from_value=from_value,
            to_value=to_value,
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
                # Take the first non-empty line, cap length defensively.
                first_line = next(
                    (ln.strip() for ln in text.splitlines() if ln.strip()),
                    "",
                )
                first_line = first_line[:600]
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
        """Run the batched vote round and apply the outcome."""
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
                    max_tokens=self.cfg.inference.max_tokens_per_decision,
                    json_mode=True,
                    stub_kind="vote",
                )
            )

        raw_results = await self.inference.generate_batch(reqs)

        # Parse, retry malformed once individually, default to 'no' otherwise.
        votes: list[tuple[str, str, str]] = []
        retries: list[tuple[int, Agent]] = []
        for i, (ag, raw) in enumerate(zip(order, raw_results)):
            parsed = parse_json_lenient(raw, want_keys=("vote",))
            if parsed and parsed.get("vote") in ("yes", "no"):
                votes.append((ag.id, parsed["vote"], str(parsed.get("reasoning", ""))))
            else:
                retries.append((i, ag))

        if retries:
            retry_reqs = [reqs[i] for i, _ in retries]
            retry_raw = await self.inference.generate_batch(retry_reqs)
            for (i, ag), raw in zip(retries, retry_raw):
                parsed = parse_json_lenient(raw, want_keys=("vote",))
                if parsed and parsed.get("vote") in ("yes", "no"):
                    votes.append(
                        (ag.id, parsed["vote"], str(parsed.get("reasoning", "")))
                    )
                else:
                    votes.append((ag.id, "no", "malformed response"))

        # Persist votes
        yes = no = 0
        for vid, v, reasoning in votes:
            await asyncio.to_thread(
                self.store.record_vote, proposal.id, vid, v, reasoning
            )
            if v == "yes":
                yes += 1
            else:
                no += 1

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
                "target_id": proposal.target_id,
                "change_type": proposal.change_type,
                "to_value": proposal.to_value,
            },
        )
        self.console.log(
            f"[bold]VOTE CLOSED[/] #{proposal.id}: yes={yes} no={no} → {outcome}"
        )

        if outcome == "approved":
            await self._apply(proposal, target)

        # Reset everyone to idle
        for a in self.agents.values():
            a.state = AgentState.IDLE
            await asyncio.to_thread(
                self.store.update_agent_state, a.id, state=a.state.value
            )

        result = {
            "proposal_id": proposal.id,
            "outcome": outcome,
            "yes": yes,
            "no": no,
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
