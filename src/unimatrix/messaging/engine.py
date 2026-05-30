"""Messaging engine — tick-based asynchronous messages.

Each tick an agent may send a batch of messages (each addressed to one or more recipients) 
as part of its decision; recipients read them from their inbox on the NEXT tick and may reply.
No agent is ever locked into a "conversation".

Delivery invariant (double-buffered, accumulating):
  - ``_inbox``      — unread messages readable THIS tick (consumed on read).
  - ``_next_inbox`` — messages SENT this tick, delivered NEXT tick.

Memory is maintained by a periodic per-agent reflection (``run_reflections``)
that summarizes recent exchanges into ``memory_summaries`` + the vector store and
refreshes per-person impressions — reusing the existing summary/impression
prompt builders.
"""
from __future__ import annotations

import asyncio
import zlib
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from rich.console import Console

from ..agents import Agent, PromptBuilder
from ..config import Config
from ..inference import GenerationRequest, InferenceClient
from ..memory import MemoryManager
from ..persistence import RunStore


@dataclass
class Message:
    id: int
    sender_id: str
    recipient_ids: list[str]
    content: str
    tick_no: int
    ts: str = ""
    is_broadcast: bool = False


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


class MessagingModule:
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
        # Double buffer (see module docstring).
        self._inbox: dict[str, list[Message]] = {}
        self._next_inbox: dict[str, list[Message]] = {}
        # Per-pair message counter (normalized key) driving impression updates.
        self._pair_msg_count: dict[tuple[str, str], int] = {}
        self._pair_impression_count: dict[tuple[str, str], int] = {}
        # Reflection bookkeeping.
        self._last_read_tick: dict[str, int] = {}
        self._last_reflection_tick: dict[str, int] = {}
        self._msgs_since_reflection: dict[str, int] = {}
        # Small ring buffer of recently-sent messages for the live snapshot.
        self._recent_sent: deque[Message] = deque(maxlen=40)
        self._last_message_tick: int = 0

    # ----- send / read / swap -----

    async def send(
        self,
        sender_id: str,
        recipient_ids: Iterable[str],
        content: str,
        tick_no: int,
        is_broadcast: bool = False,
    ) -> Message | None:
        """Persist a message and queue it for delivery next tick. Returns the
        Message, or None if it had no valid recipients."""
        content = (content or "").strip()
        if not content:
            return None
        seen: set[str] = set()
        recips: list[str] = []
        cap = self.cfg.messaging.max_recipients_per_message
        for r in recipient_ids:
            if r == sender_id or r in seen or r not in self.agents:
                continue
            recips.append(r)
            seen.add(r)
            if len(recips) >= cap:
                break
        if not recips:
            return None
        mid = await asyncio.to_thread(
            self.store.add_message, sender_id, recips, content, tick_no
        )
        msg = Message(
            id=mid,
            sender_id=sender_id,
            recipient_ids=recips,
            content=content,
            tick_no=tick_no,
            is_broadcast=is_broadcast,
        )
        for r in recips:
            self._next_inbox.setdefault(r, []).append(msg)
            key = _pair_key(sender_id, r)
            self._pair_msg_count[key] = self._pair_msg_count.get(key, 0) + 1
        # social_need: the sender gains once for reaching out.
        await self._credit(sender_id, self.cfg.social.social_need_gain_per_turn)
        self._msgs_since_reflection[sender_id] = (
            self._msgs_since_reflection.get(sender_id, 0) + 1
        )
        self._recent_sent.append(msg)
        self._last_message_tick = tick_no
        return msg

    async def deliver_broadcast(
        self, sender_id: str, content: str, tick_no: int
    ) -> None:
        """Deliver a broadcast into every agent's next-tick inbox. Ephemeral —
        not persisted as a `messages` row (broadcasts live in public_events)."""
        content = (content or "").strip()
        if not content:
            return
        recips = [aid for aid in self.agents if aid != sender_id]
        if not recips:
            return
        msg = Message(
            id=0,
            sender_id=sender_id,
            recipient_ids=recips,
            content=content,
            tick_no=tick_no,
            is_broadcast=True,
        )
        for r in recips:
            self._next_inbox.setdefault(r, []).append(msg)
        self._last_message_tick = tick_no

    async def read_inbox(self, agent_id: str, current_tick: int) -> list[Message]:
        """Consume and return the agent's unread inbox, crediting social_need
        and advancing its read/reflection bookkeeping.

        `_last_read_tick` tracks the highest tick_no the agent has CONSUMED (not
        the current tick) so resume can re-derive the unread set as
        ``tick_no > last_read_tick`` — messages sent on tick T are delivered at
        the end of T and only ever read on a later tick, so this is exact.
        """
        msgs = self._inbox.pop(agent_id, [])
        if msgs:
            await self._credit(
                agent_id,
                self.cfg.social.social_need_gain_per_turn * len(msgs),
            )
            self._msgs_since_reflection[agent_id] = (
                self._msgs_since_reflection.get(agent_id, 0) + len(msgs)
            )
            self._last_read_tick[agent_id] = max(
                [self._last_read_tick.get(agent_id, 0)] + [m.tick_no for m in msgs]
            )
        return msgs

    def swap(self) -> None:
        """Single swap site: merge this tick's sends into the unread inbox."""
        for r, msgs in self._next_inbox.items():
            self._inbox.setdefault(r, []).extend(msgs)
        self._next_inbox = {}

    def has_pending(self, agent_id: str) -> bool:
        return bool(self._inbox.get(agent_id))

    async def _credit(self, agent_id: str, amount: float) -> None:
        ag = self.agents.get(agent_id)
        if ag is None or amount <= 0:
            return
        ag.social_need = min(100.0, ag.social_need + amount)
        await asyncio.to_thread(
            self.store.update_agent_state, agent_id, social_need=ag.social_need
        )

    # ----- reflection -----

    def _as_history(self, msgs: list[dict]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for m in msgs:
            sid = m.get("sender_id")
            name = self.agents[sid].name if sid in self.agents else (sid or "?")
            out.append((name, m.get("content") or ""))
        return out

    async def run_reflections(self, current_tick: int) -> None:
        """Per-agent reflection, staggered across ticks by a stable hash of the
        agent id so they never all fire on the same tick. An agent reflects only
        if it has exchanged at least `reflection_min_new_messages` since its last
        reflection."""
        interval = self.cfg.messaging.reflection_interval_ticks
        min_new = self.cfg.messaging.reflection_min_new_messages
        window = self.cfg.messaging.message_history_window
        due: list[Agent] = []
        for aid, ag in self.agents.items():
            phase = (current_tick + zlib.crc32(aid.encode())) % interval
            if phase != 0:
                continue
            if self._msgs_since_reflection.get(aid, 0) < min_new:
                continue
            due.append(ag)
        if not due:
            return
        reqs: list[GenerationRequest] = []
        histories: list[tuple[Agent, list[tuple[str, str]]]] = []
        for ag in due:
            rows = await asyncio.to_thread(
                self.store.recent_messages_for, ag.id, window
            )
            history = self._as_history(rows)
            if not history:
                continue
            msgs, _ = self.prompts.summary_messages(ag, history)
            reqs.append(
                GenerationRequest(messages=msgs, max_tokens=120, stub_kind="summary")
            )
            histories.append((ag, history))
        if not reqs:
            return
        try:
            raws = await self.inference.generate_batch(reqs)
        except Exception as exc:
            self.console.log(f"[red]reflection batch failed[/]: {exc}")
            return
        for (ag, history), raw in zip(histories, raws):
            summary = (raw or "").strip()
            if summary:
                await self.memory.add_summary(ag.id, summary, tick_no=current_tick)
            self._last_reflection_tick[ag.id] = current_tick
            self._msgs_since_reflection[ag.id] = 0
            await self._update_impressions(ag)
        self.console.log(f"[dim]reflection: {len(histories)} agent(s)[/]")

    async def _update_impressions(self, observer: Agent) -> None:
        """Refresh the observer's impression of each peer whose exchange count
        has grown by at least N since the last update."""
        n = self.cfg.memory.person_impression_update_every_n_turns
        window = self.cfg.messaging.message_history_window
        for key, count in list(self._pair_msg_count.items()):
            if observer.id not in key:
                continue
            last = self._pair_impression_count.get(key, 0)
            if count - last < n:
                continue
            other_id = key[0] if key[1] == observer.id else key[1]
            subject = self.agents.get(other_id)
            if subject is None:
                continue
            exchange = await asyncio.to_thread(
                self.store.messages_between, observer.id, subject.id, window
            )
            ex_hist = self._as_history(exchange)
            if not ex_hist:
                continue
            prev = await self.memory.person_impression(observer.id, subject.id)
            msgs, _ = self.prompts.impression_messages(
                observer, subject, prev, ex_hist
            )
            try:
                updated = (
                    await self.inference.generate(
                        GenerationRequest(
                            messages=msgs, max_tokens=80,
                            stub_kind="person_impression",
                        )
                    )
                ).strip()
            except Exception:
                continue
            if updated:
                await self.memory.update_person_impression(
                    observer.id, subject.id, updated
                )
            self._pair_impression_count[key] = count

    # ----- snapshot + checkpoint/resume -----

    def recent_messages(self, k: int = 12) -> list[dict]:
        out: list[dict] = []
        for m in list(self._recent_sent)[-k:][::-1]:
            out.append({
                "id": m.id,
                "sender_id": m.sender_id,
                "sender_name": (
                    self.agents[m.sender_id].name
                    if m.sender_id in self.agents else m.sender_id
                ),
                "recipients": m.recipient_ids,
                "recipient_names": [
                    self.agents[r].name if r in self.agents else r
                    for r in m.recipient_ids
                ],
                "content": m.content,
                "tick_no": m.tick_no,
                "is_broadcast": m.is_broadcast,
            })
        return out

    def checkpoint_state(self) -> dict:
        """Bookkeeping the orchestrator embeds in its checkpoint. The inbox
        itself is NOT serialized — it is re-derived from the DB on resume."""
        return {
            "last_read_tick": dict(self._last_read_tick),
            "last_reflection_tick": dict(self._last_reflection_tick),
            "pair_msg_count": [
                [a, b, c] for (a, b), c in self._pair_msg_count.items()
            ],
            "pair_impression_count": [
                [a, b, c] for (a, b), c in self._pair_impression_count.items()
            ],
        }

    async def restore(self, state: dict) -> None:
        """Restore bookkeeping from a checkpoint and re-derive the unread inbox
        from the DB. Unread = messages addressed to the agent with
        ``tick_no > last_read_tick`` (the highest tick the agent has consumed).
        An agent with no recorded read defaults to 0, so it correctly recovers
        ALL of its unread mail (the inbox accumulates and is never dropped)."""
        state = state or {}
        self._last_read_tick = {
            k: int(v) for k, v in (state.get("last_read_tick") or {}).items()
        }
        self._last_reflection_tick = {
            k: int(v) for k, v in (state.get("last_reflection_tick") or {}).items()
        }
        self._pair_msg_count = {
            _pair_key(a, b): int(c)
            for a, b, c in (state.get("pair_msg_count") or [])
        }
        self._pair_impression_count = {
            _pair_key(a, b): int(c)
            for a, b, c in (state.get("pair_impression_count") or [])
        }
        self._inbox = {}
        for aid in self.agents:
            after = self._last_read_tick.get(aid, 0)
            rows = await asyncio.to_thread(
                self.store.unread_messages_for, aid, after
            )
            msgs = [
                Message(
                    id=r["id"],
                    sender_id=r["sender_id"],
                    recipient_ids=r.get("recipients", []),
                    content=r.get("content") or "",
                    tick_no=r.get("tick_no") or 0,
                    ts=r.get("ts") or "",
                )
                for r in rows
            ]
            if msgs:
                self._inbox[aid] = msgs
