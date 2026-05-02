"""Conversation engine — 1to1 / group dialogues.

A conversation is a stateful object owned by the engine; the orchestrator
calls `take_turn` for each active conversation per tick. End conditions:
  - natural: a participant ends their reply with the [LEAVE] tag
  - max_turns reached
  - external interrupt (broadcast or vote) — driven by the orchestrator

On close: per-agent LLM summary, write to memory_summaries + vector store,
optional per-person impression update, then a cooldown is recorded so the same
participants cannot immediately re-form (prevents A-B-A-B loops).
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Iterable

from rich.console import Console

from ..agents import Agent, AgentState, PromptBuilder
from ..config import Config
from ..inference import GenerationRequest, InferenceClient
from ..memory import MemoryManager
from ..persistence import RunStore


_LEAVE_RE = re.compile(r"\[LEAVE\]\s*$", re.IGNORECASE)


@dataclass
class Conversation:
    id: int
    type: str  # '1to1' | 'group'
    participant_ids: list[str]
    initiator_id: str
    history: list[tuple[str, str]] = field(default_factory=list)  # (display name, text)
    turn_index: int = 0
    next_speaker_id: str | None = None
    leave_requests: set[str] = field(default_factory=set)
    closed: bool = False
    end_reason: str | None = None
    paused: bool = False
    speaker_turns: dict[str, int] = field(default_factory=dict)

    def is_full(self, max_turns: int) -> bool:
        return self.turn_index >= max_turns

    def everyone_spoke(self) -> bool:
        return all(self.speaker_turns.get(pid, 0) >= 1 for pid in self.participant_ids)


class ConversationEngine:
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
        self.active: dict[int, Conversation] = {}
        # Conversations paused by an interrupt (vote in progress). They are
        # restored to `active` by resume_all().
        self._suspended: dict[int, Conversation] = {}
        # cooldowns keyed by frozenset of participant ids → wall-clock seconds
        self._cooldowns: dict[frozenset, float] = {}
        # pair turn counters used by the per-N-turns impression updater
        self._pair_turn_count: dict[tuple[str, str], int] = {}

    # ----- cooldown lookup, used by orchestrator when filtering targets -----

    def is_on_cooldown(self, ids: Iterable[str], now: float | None = None) -> bool:
        key = frozenset(ids)
        last = self._cooldowns.get(key)
        if last is None:
            return False
        ref = now if now is not None else time.time()
        return (ref - last) < self.cfg.conversation.cooldown_seconds_after_end

    # ----- opening conversations -----

    async def open_1to1(
        self, initiator: Agent, target: Agent, topic: str
    ) -> Conversation | None:
        if target.is_busy() or target.state == AgentState.VOTING:
            return None
        if self.is_on_cooldown([initiator.id, target.id]):
            return None
        conv_id = await asyncio.to_thread(
            self.store.open_conversation, "1to1", initiator.id, [initiator.id, target.id]
        )
        conv = Conversation(
            id=conv_id,
            type="1to1",
            participant_ids=[initiator.id, target.id],
            initiator_id=initiator.id,
            next_speaker_id=initiator.id,
        )
        if topic:
            conv.history.append(("(opening topic)", topic))
        for a in (initiator, target):
            a.state = AgentState.IN_1TO1
            a.current_conversation_id = conv_id
            await asyncio.to_thread(
                self.store.update_agent_state,
                a.id,
                state=a.state.value,
                current_conversation_id=conv_id,
            )
        self.active[conv_id] = conv
        self.console.log(
            f"[dim]conv {conv_id}[/] [cyan]1-to-1[/] "
            f"{initiator.name} ↔ {target.name}: {topic!r}"
        )
        return conv

    async def open_group(
        self,
        initiator: Agent,
        targets: list[Agent],
        topic: str,
    ) -> Conversation | None:
        eligible = [t for t in targets if not t.is_busy() and t.state != AgentState.VOTING]
        # Cap at max_group_size including the initiator
        eligible = eligible[: self.cfg.conversation.max_group_size - 1]
        if len(eligible) < 2:
            return None
        ids = [initiator.id] + [t.id for t in eligible]
        if self.is_on_cooldown(ids):
            return None
        conv_id = await asyncio.to_thread(
            self.store.open_conversation, "group", initiator.id, ids
        )
        conv = Conversation(
            id=conv_id,
            type="group",
            participant_ids=ids,
            initiator_id=initiator.id,
            next_speaker_id=initiator.id,
        )
        if topic:
            conv.history.append(("(opening topic)", topic))
        for a in [initiator] + eligible:
            a.state = AgentState.IN_GROUP
            a.current_conversation_id = conv_id
            await asyncio.to_thread(
                self.store.update_agent_state,
                a.id,
                state=a.state.value,
                current_conversation_id=conv_id,
            )
        self.active[conv_id] = conv
        self.console.log(
            f"[dim]conv {conv_id}[/] [magenta]group[/] "
            f"({len(ids)}) led by {initiator.name}: {topic!r}"
        )
        return conv

    # ----- joining an open group -----

    async def join_group(self, agent: Agent, conv_id: int) -> bool:
        conv = self.active.get(conv_id)
        if not conv or conv.type != "group" or conv.closed:
            return False
        if len(conv.participant_ids) >= self.cfg.conversation.max_group_size:
            return False
        if agent.id in conv.participant_ids:
            return False
        conv.participant_ids.append(agent.id)
        await asyncio.to_thread(
            self.store.update_conversation_participants,
            conv.id,
            conv.participant_ids,
        )
        agent.state = AgentState.IN_GROUP
        agent.current_conversation_id = conv_id
        await asyncio.to_thread(
            self.store.update_agent_state,
            agent.id,
            state=agent.state.value,
            current_conversation_id=conv_id,
        )
        return True

    # ----- per-tick turn -----

    async def take_turn(self, conv: Conversation) -> bool:
        """Run one speaker turn. Returns False if the conversation closed."""
        if conv.closed or conv.paused:
            return False
        # Honor max_turns only after every participant has had a chance to speak.
        full = conv.is_full(self.cfg.conversation.max_turns_per_conversation)
        if full and conv.everyone_spoke():
            await self.close(conv, end_reason="max_turns")
            return False
        # If we ran past max_turns but somebody never spoke, force a silent
        # participant up next so they get their say before we close.
        if full:
            silent = next(
                (pid for pid in conv.participant_ids
                 if pid not in conv.leave_requests and conv.speaker_turns.get(pid, 0) == 0),
                None,
            )
            if silent:
                conv.next_speaker_id = silent
        speaker_id = conv.next_speaker_id or conv.initiator_id
        speaker = self.agents.get(speaker_id)
        if speaker is None:
            await self.close(conv, end_reason="speaker_missing")
            return False

        peers = [self.agents[i] for i in conv.participant_ids if i in self.agents]
        # Person impressions for this speaker about co-participants
        impressions: dict[str, str] = {}
        for p in peers:
            if p.id == speaker.id:
                continue
            imp = await self.memory.person_impression(speaker.id, p.id)
            if imp:
                impressions[p.name] = imp
        medium = await self.memory.medium_term(speaker.id)
        long_q = " ".join(t for _, t in conv.history[-3:]) or "current discussion"
        long_term = [r.text for r in await self.memory.long_term(speaker.id, long_q)]

        msgs, stub_ctx = self.prompts.conversation_turn_messages(
            speaker, peers, conv.history, impressions, medium, long_term
        )
        req = GenerationRequest(
            messages=msgs,
            max_tokens=self.cfg.inference.max_tokens_per_message,
            stub_kind="chat",
            stub_context=stub_ctx,
        )
        text = await self.inference.generate(req)
        text = (text or "").strip()
        # Strip control tags for storage but record the side-effects.
        wants_leave = bool(_LEAVE_RE.search(text))
        clean = _LEAVE_RE.sub("", text).strip()
        if not clean:
            clean = "..."
        conv.turn_index += 1
        conv.speaker_turns[speaker.id] = conv.speaker_turns.get(speaker.id, 0) + 1
        conv.history.append((speaker.name, clean))
        await asyncio.to_thread(
            self.store.append_message, conv.id, conv.turn_index, speaker.id, clean
        )

        # social_need gain for everyone present (speaker and listeners)
        for p in peers:
            p.social_need = min(100.0, p.social_need + self.cfg.social.social_need_gain_per_turn)
            await asyncio.to_thread(
                self.store.update_agent_state, p.id, social_need=p.social_need
            )

        # bump pair-turn counters for impression updates
        for p in peers:
            if p.id == speaker.id:
                continue
            key = (speaker.id, p.id) if speaker.id < p.id else (p.id, speaker.id)
            self._pair_turn_count[key] = self._pair_turn_count.get(key, 0) + 1

        # Handle leave request — but only honor it once everyone has spoken at
        # least once. Otherwise the conversation would die before the other
        # participants get a turn.
        if wants_leave and conv.everyone_spoke():
            conv.leave_requests.add(speaker.id)
            # In 1-to-1 a single leave is enough; in group, when at most one
            # remains we end. (Spec: "the others accept" — we treat acceptance
            # as automatic for v1.)
            remaining = [
                i for i in conv.participant_ids if i not in conv.leave_requests
            ]
            if conv.type == "1to1" or len(remaining) < 2:
                await self.close(conv, end_reason="natural")
                return False

        # Choose next speaker
        conv.next_speaker_id = self._pick_next_speaker(conv, speaker)
        return True

    def _pick_next_speaker(self, conv: Conversation, current: Agent) -> str:
        # Round-robin among non-leavers, skipping current.
        order = [p for p in conv.participant_ids if p not in conv.leave_requests]
        if len(order) <= 1:
            return current.id
        idx = order.index(current.id) if current.id in order else -1
        return order[(idx + 1) % len(order)]

    # ----- close -----

    async def close(self, conv: Conversation, end_reason: str) -> None:
        if conv.closed:
            return
        conv.closed = True
        conv.end_reason = end_reason
        # Per-agent summary; we use one summary as conversation.summary too
        # (the same text seeds every participant's memory).
        any_agent = self.agents.get(conv.participant_ids[0])
        summary_text = ""
        if any_agent and conv.history:
            msgs, _ = self.prompts.summary_messages(any_agent, conv.history)
            req = GenerationRequest(
                messages=msgs, max_tokens=120, stub_kind="summary"
            )
            try:
                summary_text = (await self.inference.generate(req)).strip()
            except Exception:
                summary_text = "(summary unavailable)"
        await asyncio.to_thread(
            self.store.close_conversation, conv.id, end_reason, summary_text
        )

        # Per-participant memory write + maybe per-person impression update.
        for pid in conv.participant_ids:
            ag = self.agents.get(pid)
            if not ag:
                continue
            if summary_text:
                await self.memory.add_conversation_summary(
                    ag.id, conv.id, summary_text
                )
            ag.state = AgentState.IDLE
            ag.current_conversation_id = None
            await asyncio.to_thread(
                self.store.update_agent_state,
                ag.id,
                state=ag.state.value,
                current_conversation_id=None,
            )
            # Update impressions for pairs that hit the threshold
            n = self.cfg.memory.person_impression_update_every_n_turns
            for other_id in conv.participant_ids:
                if other_id == ag.id:
                    continue
                key = (ag.id, other_id) if ag.id < other_id else (other_id, ag.id)
                count = self._pair_turn_count.get(key, 0)
                if count and count % n == 0:
                    await self._update_impression(ag, self.agents[other_id], conv.history)

        self._cooldowns[frozenset(conv.participant_ids)] = time.time()
        self.active.pop(conv.id, None)
        self.console.log(
            f"[dim]conv {conv.id}[/] [yellow]closed[/] ({end_reason})"
        )

    async def _update_impression(
        self, observer: Agent, subject: Agent, history: list[tuple[str, str]]
    ) -> None:
        prev = await self.memory.person_impression(observer.id, subject.id)
        msgs, _ = self.prompts.impression_messages(observer, subject, prev, history)
        req = GenerationRequest(
            messages=msgs, max_tokens=80, stub_kind="person_impression"
        )
        try:
            updated = (await self.inference.generate(req)).strip()
        except Exception:
            return
        if updated:
            await self.memory.update_person_impression(observer.id, subject.id, updated)

    # ----- bulk operations used by interrupts -----

    async def close_all(self, end_reason: str) -> None:
        # Drain both active and suspended convs (used on pause/shutdown).
        for conv in list(self.active.values()):
            await self.close(conv, end_reason=end_reason)
        for conv in list(self._suspended.values()):
            self._suspended.pop(conv.id, None)
            self.active[conv.id] = conv  # close() removes from .active
            conv.paused = False
            await self.close(conv, end_reason=end_reason)

    async def suspend_all(self, reason: str) -> None:
        """Pause every active conversation without closing it.

        Used when a vote opens. The orchestrator calls `resume_all()` after
        the vote is tallied to put participants back into their dialogue.
        """
        for conv in list(self.active.values()):
            conv.paused = True
            self._suspended[conv.id] = conv
            self.active.pop(conv.id, None)
        if self._suspended:
            self.console.log(
                f"[dim]suspended {len(self._suspended)} conversation(s) "
                f"({reason})[/]"
            )

    async def resume_all(self) -> None:
        """Restore every suspended conversation.

        Re-attaches participants' agent state to IN_1TO1 / IN_GROUP and the
        conversation_id, since the vote module reset everyone to IDLE.
        """
        if not self._suspended:
            return
        resumed = 0
        for conv in list(self._suspended.values()):
            conv.paused = False
            self._suspended.pop(conv.id, None)
            self.active[conv.id] = conv
            target_state = (
                AgentState.IN_1TO1 if conv.type == "1to1" else AgentState.IN_GROUP
            )
            for pid in conv.participant_ids:
                ag = self.agents.get(pid)
                if not ag:
                    continue
                ag.state = target_state
                ag.current_conversation_id = conv.id
                await asyncio.to_thread(
                    self.store.update_agent_state,
                    ag.id,
                    state=ag.state.value,
                    current_conversation_id=conv.id,
                )
            resumed += 1
        self.console.log(f"[dim]resumed {resumed} conversation(s)[/]")

    def inject_external_line(self, line: str) -> None:
        """Splice a non-participant utterance (broadcast) into every active
        and suspended conversation's history so participants can react."""
        for conv in list(self.active.values()) + list(self._suspended.values()):
            conv.history.append(("(broadcast)", line))
