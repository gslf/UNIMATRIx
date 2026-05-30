"""Main asyncio orchestrator.

Owns the world: agents, messaging, votes, broadcast queue, registry row.

Tick (every cfg.simulation.tick_interval_seconds):
  1. Decay social_need (everyone).
  2. Economy pass; if the treasury collapses, end the run.
  3. If it's an election tick (or one is mid-flight) → run it atomically and
     return early (decisions/messaging skipped; pending inboxes preserved).
  4. Social mobility recompute.
  5. Drain the broadcast queue (1 broadcast per tick → every agent's inbox).
  6. Unified decision pass: each available agent reads its inbox and emits one
     decision with optional outgoing `messages` (delivered NEXT tick) and one
     optional flat power `action` (applied this tick, in declaration order so
     we don't double-book targets). A single swap site then makes this tick's
     sends readable next tick; a staggered reflection pass updates memory.
  7. Anti-silence: if no message was sent for too long, force-trigger.
  8. Auto-checkpoint on its own cadence.

Concurrency: all LLM calls are awaited inside `asyncio.gather`/`generate_batch`
so vLLM can batch them server-side; the inference client caps in-flight requests
with a semaphore (InferenceConfig.max_concurrent_requests) so a slower backend
isn't flooded with one request per agent all at once.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from ..agents import Agent, AgentState, PromptBuilder
from ..config import Config
from ..messaging import MessagingModule
from ..economy import EconomyModule
from ..inference import GenerationRequest, InferenceClient
from ..inference.client import parse_json_lenient
from ..memory import MemoryManager
from ..persistence import Registry, RunStore, utc_now_iso
from ..voting import ElectionModule
from . import silence, social_need
from .mobility import MobilityEngine


@dataclass
class BroadcastItem:
    sender_id: str  # "HUMAN" for human-issued
    message: str


class Orchestrator:
    def __init__(
        self,
        cfg: Config,
        store: RunStore,
        memory: MemoryManager,
        inference: InferenceClient,
        registry: Registry,
        run_id: int,
        run_dir: Path,
        console: Console | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.memory = memory
        self.inference = inference
        self.registry = registry
        self.run_id = run_id
        self.run_dir = run_dir
        self.console = console or Console()
        self.prompts = PromptBuilder(cfg)
        self.agents: dict[str, Agent] = {}
        self._rng = random.Random(cfg.simulation.seed)
        self.messaging = MessagingModule(
            cfg, store, memory, inference, self.prompts, self.agents, self.console
        )
        self.economy = EconomyModule(cfg, store, self.agents, self.console)
        self.mobility = MobilityEngine(cfg, store, self.agents, self.console)
        # Elections are the only vote mechanism; they need the seeded RNG (for
        # deterministic tie-breaks) and the mobility engine (role/prestige
        # helpers). Exposed as `self.voting` so existing call sites keep working.
        self.voting = ElectionModule(
            cfg, store, memory, inference, self.prompts, self.agents,
            rng=self._rng, mobility=self.mobility, console=self.console,
        )
        self._broadcast_queue: list[BroadcastItem] = []
        self._paused = asyncio.Event()
        self._paused.set()  # set means NOT paused; clear() to pause
        self._stop = asyncio.Event()
        self._last_tick_ts = time.monotonic()
        self._last_activity_ts = time.monotonic()
        self._last_checkpoint_ts = time.monotonic()
        self._tick_no = 0
        self.metrics: dict = {
            "decisions_attempted": 0,
            "decisions_unparsed": 0,
            "decisions_skipped": 0,        # rejected at _apply_decision (bad target, etc.)
            "decisions_by_action": {},     # action -> count (what the LLM picked)
            "actions_applied": {},          # action -> count (what actually went through)
            "messages_sent": 0,
            "elections_held": 0,
            "loans_requested": 0,
            "loans_granted": 0,
            "loans_denied": 0,
            "steals_attempted": 0,
            "steals_succeeded": 0,
            "gifts_made": 0,
            "sabotages_attempted": 0,
            "sabotages_succeeded": 0,
            "influence_actions": 0,
            "office_powers_used": 0,
            "inference_errors": 0,
            "last_error": None,
            "last_error_ts": None,
            "last_decision_raw": None,     # last raw LLM response (truncated)
            "last_skip_reason": None,
            "community_balance": 0.0,
            "failure_reason": None,
            "inference": {},               # last decisions-batch timing/token stats
        }

    # ----- bootstrap -----

    async def initialize(self) -> None:
        # 1) Build agents; derive prestige from the initial role and popularity
        #    from the initial class's floor so tick-1 mobility is stable.
        for spec in self.cfg.agents:
            ag = Agent.from_spec(
                spec,
                self.cfg.social.social_need_initial,
                initial_balance=0.0,  # the economy module seeds balances atomically
            )
            ag.prestige = float(self.mobility.role_prestige(ag.role))
            ag.popularity = self.mobility.initial_popularity_for_class(ag.klass)
            self.agents[ag.id] = ag

        # 2) Office reconciliation: each elected office must have EXACTLY one
        #    holder. Keep the first agent (config order) in each office role;
        #    demote any extras to an ordinary role derived from the office's
        #    nominal prestige. Officeholders sit in the top class while serving.
        for oid in self.cfg.office_ids():
            holders = [a for a in self.agents.values() if a.role == oid]
            for i, ag in enumerate(holders):
                if i == 0:
                    ag.office = oid
                    ag.klass = self.mobility.top_class
                else:
                    ag.office = None
                    ag.role = self.mobility.ordinary_role_for_prestige(
                        self.mobility.role_prestige(oid)
                    )
                    ag.prestige = float(self.mobility.role_prestige(ag.role))
                    self.console.log(
                        f"[yellow]init reconcile[/]: extra {oid} {ag.id} "
                        f"→ {ag.role}"
                    )

        # 3) Persist initial state + a single reconciled 'initial' status row
        #    per agent (role + class) so the graph renderer has its baseline.
        for ag in self.agents.values():
            await asyncio.to_thread(self.store.upsert_agent, ag.to_db_row())
            await asyncio.to_thread(
                self.store.record_status_change, ag.id, "initial", None, ag.role
            )
            await asyncio.to_thread(
                self.store.record_status_change, ag.id, "initial", None, ag.klass
            )

        # 4) Seed the community treasury and per-agent balances (idempotent).
        await self.economy.initialize()

    async def initialize_from_existing(self) -> None:
        """Re-attach to a previously-paused run.

        Agents are reconstructed from cfg.agents (for personality/values/
        backstory/opinions) and overlaid with their stored runtime state
        (role/class/social_need/state/balance). Tick counter and broadcast
        queue are restored from the latest checkpoint when present. The unread
        message inbox is re-derived from the DB (see `messaging.restore`).
        """
        rows_by_id = {
            r["agent_id"]: r
            for r in await asyncio.to_thread(self.store.list_agents)
        }
        for spec in self.cfg.agents:
            ag = Agent.from_spec(
                spec,
                self.cfg.social.social_need_initial,
                initial_balance=0.0,
            )
            row = rows_by_id.get(ag.id)
            if row is not None:
                if row.get("role"):
                    ag.role = row["role"]
                if row.get("class"):
                    ag.klass = row["class"]
                sn = row.get("social_need")
                if sn is not None:
                    ag.social_need = float(sn)
                try:
                    ag.state = AgentState(row.get("state") or "idle")
                except ValueError:
                    # Old DBs may carry retired states (in_1to1, …); the guard
                    # collapses any unknown value to IDLE.
                    ag.state = AgentState.IDLE
                if ag.state == AgentState.VOTING:
                    ag.state = AgentState.IDLE
                bal = row.get("bank_account")
                if bal is not None:
                    ag.bank_account = float(bal)
                ag.destitute = bool(row.get("destitute") or 0)
                # Social-mobility attributes — the DB is authoritative on
                # resume. Do NOT re-derive or re-run office reconciliation
                # (that would flap offices/roles every resume).
                pr = row.get("prestige")
                if pr is not None:
                    ag.prestige = float(pr)
                po = row.get("popularity")
                if po is not None:
                    ag.popularity = float(po)
                ag.office = row.get("office") or None
            self.agents[ag.id] = ag
        # Idempotent — won't overwrite an existing treasury balance.
        await self.economy.initialize()
        # Restore loop scalars from the most recent checkpoint, if any.
        ckpt = await asyncio.to_thread(self.store.latest_checkpoint)
        messaging_state: dict = {}
        if ckpt:
            try:
                self._tick_no = int(ckpt.get("tick_no") or 0)
            except (TypeError, ValueError):
                self._tick_no = 0
            for item in ckpt.get("broadcast_queue") or []:
                if isinstance(item, dict) and item.get("message"):
                    self._broadcast_queue.append(
                        BroadcastItem(
                            item.get("sender_id") or "HUMAN",
                            str(item["message"]),
                        )
                    )
            messaging_state = ckpt.get("messaging") or {}
        # Re-derive the unread inbox from the DB (never serialized) using each
        # agent's recorded last-consumed tick.
        await self.messaging.restore(messaging_state)

    # ----- public controls -----

    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()

    def is_paused(self) -> bool:
        return not self._paused.is_set()

    def request_stop(self) -> None:
        self._stop.set()
        self._paused.set()  # release any pending pause-await

    async def queue_broadcast(self, sender_id: str, message: str) -> str:
        """If a vote is active, queue. Otherwise apply at the next tick boundary."""
        msg = (message or "").strip()
        if not msg:
            return "empty"
        item = BroadcastItem(sender_id, msg)
        self._broadcast_queue.append(item)
        if self.voting.is_active():
            return "queued"
        return "scheduled"

    async def force_checkpoint(self) -> int:
        return await self._write_checkpoint()

    # ----- main loop -----

    async def run(self) -> None:
        try:
            while not self._stop.is_set():
                # If paused, wait without spinning
                if not self._paused.is_set():
                    await self._on_pause_entered()
                    await self._paused.wait()
                    self._last_tick_ts = time.monotonic()
                await self._tick()
                # Auto checkpoint
                if (
                    time.monotonic() - self._last_checkpoint_ts
                ) >= self.cfg.simulation.auto_checkpoint_minutes * 60:
                    await self._write_checkpoint()
                # Sleep the remainder of the tick
                drift = time.monotonic() - self._last_tick_ts
                sleep_for = max(0.0, self.cfg.simulation.tick_interval_seconds - drift)
                # Allow pause/stop to interrupt the sleep
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
                    break
                except asyncio.TimeoutError:
                    pass
        finally:
            # Don't overwrite a terminal failure status with "ended".
            if self.metrics.get("failure_reason"):
                pass
            else:
                self.registry.update_status(self.run_id, "ended", utc_now_iso())

    async def _on_pause_entered(self) -> None:
        self.console.log("[bold red]paused[/]; checkpointing")
        await self._write_checkpoint()
        await asyncio.to_thread(
            self.store.record_event, "pause", {"ts": utc_now_iso()}
        )
        self.registry.update_status(self.run_id, "paused")

    async def _tick(self) -> None:
        self._tick_no += 1
        now = time.monotonic()
        self._last_tick_ts = now

        # 1) decay social need (+ optional prestige/popularity decay).
        social_need.decay(
            self.agents.values(), self.cfg.social.social_need_decay_per_tick
        )
        await self.mobility.decay()

        # 2) economy pass: salary/tax/expense for every agent + community
        # expense. If the community goes bankrupt, end the run.
        failure = await self.economy.apply_tick()
        self.metrics["community_balance"] = await asyncio.to_thread(
            self.store.get_community_balance
        )
        if failure is not None:
            await self._end_run_failed(failure)
            return

        # 3) elections. A leftover active election (e.g. crash recovery) or a
        # scheduled election tick preempts everything else: run it atomically
        # and skip mobility/decisions/conversations this tick.
        in_warmup = self._tick_no <= self.cfg.voting.warmup_ticks
        is_election_tick = (
            not in_warmup
            and self._tick_no % self.cfg.voting.election_interval_ticks == 0
        )
        if self.voting.is_active() or is_election_tick:
            await self._run_election()
            return

        # 4) social mobility: recompute roles (from prestige) and classes (from
        # popularity + balance). Frozen during warmup.
        if not in_warmup:
            await self.mobility.recompute()

        # 5) drain broadcast queue (one per tick) — non-interruptive.
        if self._broadcast_queue:
            item = self._broadcast_queue.pop(0)
            try:
                await self._apply_broadcast(item)
            except Exception as exc:
                self.console.log(f"[red]broadcast failed[/]: {exc}")
            self._last_activity_ts = time.monotonic()

        # 6) every available agent decides: read inbox, send messages, and/or
        # take one power action. Messages are delivered next tick.
        await self._run_decisions()

        # 7) anti-silence
        await self._maybe_break_silence()

    async def _end_run_failed(self, reason: str) -> None:
        """Record a terminal failure and stop the loop cleanly."""
        self.metrics["failure_reason"] = reason
        self.console.log(f"[bold red]SIMULATION FAILED[/]: {reason}")
        await asyncio.to_thread(
            self.store.record_event,
            "simulation_failed",
            {"reason": reason, "tick": self._tick_no, "ts": utc_now_iso()},
        )
        try:
            self.registry.update_status(self.run_id, "failed", utc_now_iso())
        except Exception as exc:
            self.console.log(f"[red]could not mark registry failed[/]: {exc}")
        self.request_stop()

    async def _run_election(self) -> None:
        """Run a full society-wide election atomically within this tick.

        Every agent is moved to the VOTING state for the duration; the election
        (debate + three office ballots + reassignments) is resolved in one
        batched pass; then everyone is returned to IDLE. The cleanup runs in a
        finally so a failure mid-election can never strand the society. Pending
        message inboxes are untouched — they remain readable on the first
        post-election decision tick.
        """
        for a in self.agents.values():
            a.state = AgentState.VOTING
            await asyncio.to_thread(
                self.store.update_agent_state, a.id, state=a.state.value
            )
        try:
            await self.voting.run_election(self._tick_no)
            self.metrics["elections_held"] += 1
        except Exception as exc:
            self.console.log(f"[red]election failed[/]: {exc}")
            self.metrics["last_error"] = str(exc)
            self.metrics["last_error_ts"] = utc_now_iso()
            self.voting.active = None
        finally:
            for a in self.agents.values():
                a.state = AgentState.IDLE
                await asyncio.to_thread(
                    self.store.update_agent_state, a.id, state=a.state.value
                )
        self._last_activity_ts = time.monotonic()

    # ----- per-tick decisions -----

    def _log_inference_window(self, n_calls: int, batch_s: float) -> None:
        """Fold the just-finished decisions batch's inference stats into
        metrics["inference"] (surfaced in the web UI) and emit one concise
        console line (terminal + web "Recent events")."""
        stats = self.inference.window_stats()
        self.metrics["inference"] = {
            **stats,
            "batch_wall_s": round(batch_s, 1),
            "decisions": n_calls,
        }
        timing = (
            f" · prefill {stats['prefill_s']:.0f}s/decode {stats['decode_s']:.0f}s"
            if (stats["prefill_s"] or stats["decode_s"]) else ""
        )
        self.console.log(
            f"[dim]tick {self._tick_no}: {n_calls} decisions in {batch_s:.1f}s · "
            f"avg {stats['avg_latency_s']:.1f}s/call max {stats['max_latency_s']:.1f}s · "
            f"prompt~{stats['avg_prompt_tokens']} out~{stats['avg_completion_tokens']} tok"
            f"{timing} · peak in-flight {stats['peak_inflight']}/{stats['cap']}[/]"
        )

    async def _run_decisions(self) -> None:
        """The unified decision pass. Each selected agent reads its inbox and
        produces one decision JSON with an optional `messages` array and an
        optional flat power `action`. Messages are delivered NEXT tick; actions
        are applied this tick (sequentially, so targets aren't double-booked)."""
        agents = [a for a in self.agents.values() if a.state == AgentState.IDLE]
        if not agents:
            return
        # Randomized order each tick (deterministic per-seed) so nobody is
        # permanently first.
        self._rng.shuffle(agents)
        # Optional throttle: cap how many agents we ask the LLM about per tick.
        # Agents with unread mail come first (so replies aren't starved), then
        # the lowest social_need (they'd be forced anyway).
        cap = self.cfg.social.max_idle_decisions_per_tick
        if cap and len(agents) > cap:
            agents.sort(
                key=lambda a: (not self.messaging.has_pending(a.id), a.social_need)
            )
            agents = agents[:cap]

        recent_events = [
            f"{e['event_type']}: {json.dumps(e['payload'])[:600]}"
            for e in await asyncio.to_thread(self.store.recent_events, 10)
        ]
        peers = list(self.agents.values())

        prompts_payload: list[tuple[Agent, GenerationRequest]] = []
        for a in agents:
            forced = a.social_need < self.cfg.social.social_need_critical_threshold
            inbox = await self.messaging.read_inbox(a.id, self._tick_no)
            inbox_lines = self._render_inbox(inbox)
            query = inbox[-1].content if inbox else "what should I do now"
            medium = await self.memory.medium_term(a.id)
            long_term = [
                r.text for r in await self.memory.long_term(a.id, query)
            ]
            msgs, stub_ctx = self.prompts.decide_action_messages(
                a, peers, medium, long_term, recent_events, forced, inbox_lines,
            )
            stub_ctx["inbox_senders"] = [m.sender_id for m in inbox]
            prompts_payload.append(
                (
                    a,
                    GenerationRequest(
                        messages=msgs,
                        max_tokens=self.cfg.inference.max_tokens_per_decision,
                        json_mode=True,
                        stub_kind="decision",
                        stub_context=stub_ctx,
                        label=a.id,
                    ),
                )
            )

        # Fresh stats window so metrics["inference"] reflects THIS batch; the
        # wall time brackets the whole concurrent fan-out (not per-call).
        self.inference.reset_window()
        _t0 = time.perf_counter()
        try:
            raws = await self.inference.generate_batch(
                [r for _, r in prompts_payload]
            )
        except Exception as exc:
            # Don't kill the loop on a transient backend hiccup. Log and skip
            # this tick's decisions; timers keep running. The swap still runs in
            # `finally` so any already-queued messages are delivered.
            self.metrics["inference_errors"] += 1
            self.metrics["last_error"] = str(exc)
            self.metrics["last_error_ts"] = utc_now_iso()
            self.console.log(
                f"[red]decisions batch failed[/], skipping tick: {exc}"
            )
            self.messaging.swap()
            return
        self._log_inference_window(len(prompts_payload), time.perf_counter() - _t0)

        decisions: list[tuple[Agent, dict]] = []
        for (a, _), raw in zip(prompts_payload, raws):
            self.metrics["decisions_attempted"] += 1
            self.metrics["last_decision_raw"] = (raw or "")[:600]
            parsed = parse_json_lenient(raw, want_keys=("messages", "action"))
            if parsed is None:
                self.metrics["decisions_unparsed"] += 1
                self.console.log(
                    f"[yellow]unparseable decision[/] {a.id}: {raw[:120]!r}"
                )
                parsed = {"action": "do_nothing"}
            decisions.append((a, parsed))
            act = str(parsed.get("action") or "do_nothing")
            self.metrics["decisions_by_action"][act] = (
                self.metrics["decisions_by_action"].get(act, 0) + 1
            )

        # Phase 2a — deliver every outgoing message first (deferred, so order
        # cannot affect who sees what). Then apply flat actions sequentially.
        any_message = False
        for ag, dec in decisions:
            sent = await self._send_messages(ag, dec.get("messages"))
            any_message = any_message or sent
        for ag, dec in decisions:
            if ag.state != AgentState.IDLE:
                continue
            try:
                await self._apply_decision(ag, dec)
            except Exception as exc:  # do not let one bad decision kill the loop
                self.console.log(f"[red]decision error for {ag.id}[/]: {exc}")

        if any_message:
            self._last_activity_ts = time.monotonic()
        # Single swap site: this tick's sends become readable next tick.
        self.messaging.swap()
        # Periodic reflection (staggered) — feeds medium/long-term memory.
        await self.messaging.run_reflections(self._tick_no)

    def _render_inbox(self, inbox: list) -> list[str]:
        """Render an agent's inbox messages as prompt-ready strings."""
        lines: list[str] = []
        for m in inbox:
            sender = self.agents.get(m.sender_id)
            sender_name = sender.name if sender else (
                "the public crier" if m.sender_id == "HUMAN" else m.sender_id
            )
            others = [r for r in m.recipient_ids if r != m.sender_id]
            also = ""
            if len(others) > 1:
                also = f" (and {len(others) - 1} others)"
            tag = "[BROADCAST] " if m.is_broadcast else ""
            lines.append(f"{tag}{sender_name}{also}: {m.content}")
        return lines

    async def _send_messages(self, ag: Agent, raw_messages) -> bool:
        """Resolve and dispatch an agent's outgoing messages for this tick.
        Returns True if at least one message was sent."""
        if not isinstance(raw_messages, list) or not raw_messages:
            return False
        cap = self.cfg.messaging.max_messages_per_tick
        sent = False
        for entry in raw_messages[:cap]:
            if not isinstance(entry, dict):
                continue
            content = str(entry.get("content") or "").strip()
            if not content:
                continue
            to_refs = entry.get("to") or entry.get("targets") or []
            if isinstance(to_refs, str):
                to_refs = [to_refs]
            recips = self._resolve_targets(
                to_refs, ag.id, self.cfg.messaging.max_recipients_per_message
            )
            if not recips:
                continue
            msg = await self.messaging.send(
                ag.id, [t.id for t in recips], content, self._tick_no
            )
            if msg is not None:
                self.metrics["messages_sent"] += 1
                sent = True
        return sent

    def _resolve_agent_ref(self, ref: str | None) -> Agent | None:
        """Resolve an agent reference that the LLM produced.

        Accepts: an exact agent id ('agent_07'), a name ('Eleanor Vance'),
        a case-insensitive partial name match, or even a [id: x] / (id: x)
        decoration the model copied from the prompt.
        """
        if not ref:
            return None
        ref = str(ref).strip()
        # Strip trailing/leading punctuation often leaked from the prompt
        for prefix in ("id:", "id="):
            if ref.lower().startswith(prefix):
                ref = ref[len(prefix):].strip()
        ref = ref.strip("[](){}<>\"' ")
        if ref in self.agents:
            return self.agents[ref]
        # by name (exact, case-insensitive)
        lc = ref.lower()
        for a in self.agents.values():
            if a.name.lower() == lc:
                return a
        # by name (loose: first+last partial)
        for a in self.agents.values():
            if lc in a.name.lower():
                return a
        return None

    def _resolve_targets(
        self, refs, exclude_id: str, cap: int
    ) -> list[Agent]:
        """Resolve a list of agent refs to distinct Agents, dropping self and
        unresolved entries, capped to `cap`."""
        out: list[Agent] = []
        seen: set[str] = set()
        for r in refs or []:
            t = self._resolve_agent_ref(r)
            if t and t.id != exclude_id and t.id not in seen:
                out.append(t)
                seen.add(t.id)
            if len(out) >= cap:
                break
        return out

    def _skip(self, action: str, reason: str) -> None:
        self.metrics["decisions_skipped"] += 1
        self.metrics["last_skip_reason"] = f"{action}: {reason}"
        self.console.log(f"[yellow]{action} skipped[/]: {reason}")

    def _applied(self, action: str) -> None:
        self.metrics["actions_applied"][action] = (
            self.metrics["actions_applied"].get(action, 0) + 1
        )

    async def _apply_decision(self, ag: Agent, dec: dict) -> None:
        action = dec.get("action") or "do_nothing"
        if action != "do_nothing":
            self.console.log(
                f"[dim]decision[/] {ag.id} ({ag.name}, {ag.role}): "
                f"{action} {self._summarize_decision(dec)}"
            )
        if action == "do_nothing":
            return
        if action == "request_loan":
            self.metrics["loans_requested"] += 1
            tgt = self._resolve_agent_ref(dec.get("target"))
            if not tgt or tgt.id == ag.id:
                self._skip("request_loan", f"{ag.id}: target={dec.get('target')!r} not resolved")
                return
            financial_office = self.cfg.office_for_power("financial")
            if tgt.office != financial_office:
                self._skip(
                    "request_loan",
                    f"{ag.id}: target {tgt.id} does not hold the banker office "
                    f"(office={tgt.office})",
                )
                return
            try:
                amount = float(dec.get("amount") or 0)
            except (TypeError, ValueError):
                self._skip("request_loan", f"{ag.id}: bad amount={dec.get('amount')!r}")
                return
            if amount <= 0:
                self._skip("request_loan", f"{ag.id}: non-positive amount {amount}")
                return
            amount = min(amount, self.cfg.economy.loan_max_per_request)
            reason = str(dec.get("reason") or "").strip()
            try:
                community = await asyncio.to_thread(
                    self.store.get_community_balance
                )
                msgs, _ = self.prompts.banker_loan_messages(
                    banker=tgt,
                    borrower=ag,
                    requested_amount=amount,
                    reason=reason,
                    community_balance=community,
                )
                raw = await self.inference.generate(
                    GenerationRequest(
                        messages=msgs,
                        max_tokens=16,
                        stub_kind="loan",
                    )
                )
                approved, paid, info = await self.economy.grant_loan(
                    tgt, ag, amount, raw
                )
            except Exception as exc:
                self.metrics["inference_errors"] += 1
                self.metrics["last_error"] = str(exc)
                self.metrics["last_error_ts"] = utc_now_iso()
                self._skip("request_loan", f"{ag.id}: loan call failed: {exc}")
                return
            event_payload = {
                "borrower_id": ag.id,
                "banker_id": tgt.id,
                "requested": amount,
                "approved": approved,
                "paid": paid,
                "reason": reason,
                "info": info,
                "banker_reply": (raw or "")[:200],
            }
            if approved:
                self.metrics["loans_granted"] += 1
                await asyncio.to_thread(
                    self.store.record_event, "loan_granted", event_payload
                )
                self.console.log(
                    f"[green]loan granted[/]: {tgt.name} → {ag.name} "
                    f"{paid:.2f} (requested {amount:.2f})"
                )
            else:
                self.metrics["loans_denied"] += 1
                await asyncio.to_thread(
                    self.store.record_event, "loan_denied", event_payload
                )
                self.console.log(
                    f"[yellow]loan denied[/]: {tgt.name} → {ag.name} "
                    f"({info})"
                )
            self._applied(action)
            return
        if action in ("praise", "denounce"):
            await self._apply_influence(ag, dec, action)
            return
        if action == "steal":
            await self._apply_steal(ag, dec)
            return
        if action == "gift":
            await self._apply_gift(ag, dec)
            return
        if action == "sabotage":
            await self._apply_sabotage(ag, dec)
            return
        if action in ("decree", "ruling", "policy"):
            await self._apply_office_power(ag, dec, action)
            return
        # unknown action
        self._skip(action, f"{ag.id}: unknown action")

    # ----- ordinary agent powers -----

    def _clamp01(self, v: float) -> float:
        return max(0.0, min(100.0, float(v)))

    async def _persist_attrs(self, ag: Agent) -> None:
        await asyncio.to_thread(
            self.store.update_agent_state,
            ag.id, prestige=ag.prestige, popularity=ag.popularity,
        )

    async def _apply_influence(self, ag: Agent, dec: dict, action: str) -> None:
        cap = self.cfg.mobility.max_influence_targets
        tgts = self._resolve_targets(dec.get("targets"), ag.id, cap)
        if not tgts:
            self._skip(action, f"{ag.id}: no valid targets {dec.get('targets')!r}")
            return
        aspect = str(dec.get("aspect") or "popularity").strip().lower()
        if aspect not in ("popularity", "prestige", "both"):
            aspect = "popularity"
        step = self.cfg.mobility.influence_step
        delta = step if action == "praise" else -step
        for t in tgts:
            if aspect in ("popularity", "both"):
                t.popularity = self._clamp01(t.popularity + delta)
            if aspect in ("prestige", "both"):
                t.prestige = self._clamp01(t.prestige + delta)
            await self._persist_attrs(t)
        await asyncio.to_thread(
            self.store.record_event,
            action,
            {"actor_id": ag.id, "targets": [t.id for t in tgts],
             "aspect": aspect, "delta": delta},
        )
        self.metrics["influence_actions"] += 1
        self._applied(action)

    async def _apply_steal(self, ag: Agent, dec: dict) -> None:
        self.metrics["steals_attempted"] += 1
        tgt = self._resolve_agent_ref(dec.get("target"))
        if not tgt or tgt.id == ag.id:
            self._skip("steal", f"{ag.id}: target={dec.get('target')!r} not resolved")
            return
        try:
            amount = float(dec.get("amount") or 0)
        except (TypeError, ValueError):
            amount = self.cfg.agent_powers.steal_max
        if amount <= 0:
            amount = self.cfg.agent_powers.steal_max
        ap = self.cfg.agent_powers
        caught = self._rng.random() >= ap.steal_success_prob
        if caught:
            ag.popularity = self._clamp01(
                ag.popularity - ap.steal_caught_popularity_penalty
            )
            await self._persist_attrs(ag)
            await asyncio.to_thread(
                self.store.record_event, "theft_caught",
                {"thief_id": ag.id, "victim_id": tgt.id,
                 "penalty": ap.steal_caught_popularity_penalty},
            )
            self.console.log(f"[red]theft caught[/]: {ag.name} → {tgt.name}")
            self._applied("steal")
            return
        ok, moved, _ = await self.economy.steal(ag, tgt, amount)
        await asyncio.to_thread(
            self.store.record_event, "theft",
            {"thief_id": ag.id, "victim_id": tgt.id, "amount": moved, "ok": ok},
        )
        if ok:
            self.metrics["steals_succeeded"] += 1
            self.console.log(
                f"[magenta]theft[/]: {ag.name} took {moved:.0f} from {tgt.name}"
            )
        self._applied("steal")

    async def _apply_gift(self, ag: Agent, dec: dict) -> None:
        tgt = self._resolve_agent_ref(dec.get("target"))
        if not tgt or tgt.id == ag.id:
            self._skip("gift", f"{ag.id}: target={dec.get('target')!r} not resolved")
            return
        try:
            amount = float(dec.get("amount") or 0)
        except (TypeError, ValueError):
            self._skip("gift", f"{ag.id}: bad amount={dec.get('amount')!r}")
            return
        if amount <= 0:
            self._skip("gift", f"{ag.id}: non-positive amount {amount}")
            return
        ok, moved, _ = await self.economy.gift(ag, tgt, amount)
        if not ok:
            self._skip("gift", f"{ag.id}: no funds to give")
            return
        await asyncio.to_thread(
            self.store.record_event, "gift",
            {"giver_id": ag.id, "target_id": tgt.id, "amount": moved},
        )
        self.metrics["gifts_made"] += 1
        self.console.log(f"[green]gift[/]: {ag.name} gave {moved:.0f} to {tgt.name}")
        self._applied("gift")

    async def _apply_sabotage(self, ag: Agent, dec: dict) -> None:
        self.metrics["sabotages_attempted"] += 1
        tgt = self._resolve_agent_ref(dec.get("target"))
        if not tgt or tgt.id == ag.id or tgt.office is None:
            self._skip("sabotage", f"{ag.id}: target {dec.get('target')!r} is not an officeholder")
            return
        ap = self.cfg.agent_powers
        if self._rng.random() < ap.sabotage_success_prob:
            tgt.power_blocked = True
            self.metrics["sabotages_succeeded"] += 1
            await asyncio.to_thread(
                self.store.record_event, "sabotage",
                {"saboteur_id": ag.id, "target_id": tgt.id, "office": tgt.office},
            )
            self.console.log(f"[magenta]sabotage[/]: {ag.name} blocked {tgt.name}")
        else:
            ag.popularity = self._clamp01(
                ag.popularity - ap.sabotage_caught_popularity_penalty
            )
            await self._persist_attrs(ag)
            await asyncio.to_thread(
                self.store.record_event, "sabotage_caught",
                {"saboteur_id": ag.id, "target_id": tgt.id,
                 "penalty": ap.sabotage_caught_popularity_penalty},
            )
            self.console.log(f"[red]sabotage caught[/]: {ag.name} → {tgt.name}")
        self._applied("sabotage")

    # ----- office powers (holder only, multi-target) -----

    async def _apply_office_power(self, ag: Agent, dec: dict, action: str) -> None:
        power = self.cfg.power_of_office(ag.office) if ag.office else None
        expected = {"legislative": "decree", "judicial": "ruling",
                    "financial": "policy"}.get(power)
        if expected != action:
            self._skip(action, f"{ag.id}: not the right officeholder for {action}")
            return
        # A successful sabotage blocks exactly the next power use.
        if ag.power_blocked:
            ag.power_blocked = False
            await asyncio.to_thread(
                self.store.record_event, "power_sabotaged",
                {"office_id": ag.id, "office": ag.office, "action": action},
            )
            self.console.log(f"[red]{action} blocked by sabotage[/]: {ag.name}")
            return
        cap = self.cfg.office_powers.max_targets_per_power
        tgts = self._resolve_targets(dec.get("targets"), ag.id, cap)
        if not tgts:
            self._skip(action, f"{ag.id}: no valid targets {dec.get('targets')!r}")
            return
        op = self.cfg.office_powers
        if action == "decree":
            direction = str(dec.get("direction") or "raise").strip().lower()
            delta = op.senator_prestige_power if direction != "lower" else -op.senator_prestige_power
            for t in tgts:
                t.prestige = self._clamp01(t.prestige + delta)
                await self._persist_attrs(t)
            await asyncio.to_thread(
                self.store.record_event, "decree",
                {"senator_id": ag.id, "targets": [t.id for t in tgts],
                 "direction": direction, "delta": delta},
            )
        elif action == "ruling":
            verdict = str(dec.get("verdict") or "sanction").strip().lower()
            for t in tgts:
                if verdict == "vindicate":
                    t.popularity = self._clamp01(t.popularity + op.judge_popularity_power)
                    await self._persist_attrs(t)
                else:
                    t.popularity = self._clamp01(t.popularity - op.judge_popularity_power)
                    await self._persist_attrs(t)
                    fine = t.bank_account * op.judge_fine_fraction
                    if fine > 0:
                        await self.economy.agent_to_treasury(
                            t, fine, "fine", f"judge fine by {ag.id}"
                        )
            await asyncio.to_thread(
                self.store.record_event, "ruling",
                {"judge_id": ag.id, "targets": [t.id for t in tgts],
                 "verdict": verdict},
            )
        else:  # policy (banker)
            direction = str(dec.get("direction") or "subsidy").strip().lower()
            try:
                amount = float(dec.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            amount = min(max(0.0, amount), op.banker_transfer_max)
            for t in tgts:
                if amount <= 0:
                    break
                if direction == "levy":
                    await self.economy.agent_to_treasury(
                        t, amount, "levy", f"banker levy by {ag.id}"
                    )
                else:
                    await self.economy.treasury_to_agent(
                        t, amount, "subsidy", f"banker subsidy by {ag.id}"
                    )
            await asyncio.to_thread(
                self.store.record_event, "policy",
                {"banker_id": ag.id, "targets": [t.id for t in tgts],
                 "direction": direction, "amount": amount},
            )
        self.metrics["office_powers_used"] += 1
        self._applied(action)

    @staticmethod
    def _summarize_decision(dec: dict) -> str:
        action = dec.get("action")
        if action in ("praise", "denounce", "decree", "ruling", "policy"):
            return f"→ {dec.get('targets')}"
        if action in ("steal", "gift", "sabotage"):
            return f"→ {dec.get('target')!r}"
        return ""

    # ----- broadcasts -----

    async def _apply_broadcast(self, item: BroadcastItem) -> None:
        # A broadcast is a public event delivered into every agent's inbox for
        # the next tick. It is NOT persisted as a `messages` row (that would add
        # a hub node to the social graph); it lives in public_events + memory.
        sender = self.agents.get(item.sender_id)
        sender_label = sender.name if sender else (
            "the public crier" if item.sender_id == "HUMAN" else item.sender_id
        )
        event_type = "broadcast_human" if item.sender_id == "HUMAN" else "broadcast_agent"
        eid = await asyncio.to_thread(
            self.store.record_event,
            event_type,
            {"sender_id": item.sender_id, "message": item.message},
        )
        await self.memory.add_public_event(
            eid,
            list(self.agents.keys()),
            f"Broadcast from {sender_label}: {item.message}",
        )
        await self.messaging.deliver_broadcast(
            item.sender_id, f"[from {sender_label}] {item.message}", self._tick_no
        )
        self.console.log(
            f"[bold green]BROADCAST[/] from {sender_label}: {item.message[:80]}"
        )

    # ----- anti-silence -----

    async def _maybe_break_silence(self) -> None:
        recent_message = self.messaging._last_message_tick == self._tick_no
        if recent_message or self.voting.is_active() or self._broadcast_queue:
            self._last_activity_ts = time.monotonic()
            return
        if (time.monotonic() - self._last_activity_ts) < self.cfg.social.silence_detection_seconds:
            return
        forced = silence.pick_forced_agents(
            list(self.agents.values()),
            self.cfg.social.forced_interaction_count_on_silence,
        )
        if not forced:
            return
        self.console.log(
            f"[yellow]anti-silence trigger[/]: forcing {len(forced)} agent(s)"
        )
        # Force these agents to interact on the next decision pass by lowering
        # their social_need below the critical threshold (they will then be
        # treated as forced in `_run_decisions`).
        for a in forced:
            a.social_need = min(
                a.social_need,
                self.cfg.social.social_need_critical_threshold - 1.0,
            )
            await asyncio.to_thread(
                self.store.update_agent_state, a.id, social_need=a.social_need
            )
        self._last_activity_ts = time.monotonic()

    # ----- checkpoint -----

    async def _write_checkpoint(self) -> int:
        state = {
            "agents": [
                {
                    "agent_id": a.id,
                    "state": a.state.value,
                    "social_need": a.social_need,
                    "prestige": a.prestige,
                    "popularity": a.popularity,
                    "office": a.office,
                }
                for a in self.agents.values()
            ],
            "broadcast_queue": [
                {"sender_id": b.sender_id, "message": b.message}
                for b in self._broadcast_queue
            ],
            "active_election": (
                self.voting.active.to_dict() if self.voting.active else None
            ),
            "messaging": self.messaging.checkpoint_state(),
            "last_silence_check": self._last_activity_ts,
            "tick_no": self._tick_no,
            "ts": utc_now_iso(),
        }
        cid = await asyncio.to_thread(self.store.write_checkpoint, state)
        self._last_checkpoint_ts = time.monotonic()
        return cid

    # ----- snapshot for the web UI -----

    def snapshot(self) -> dict:
        per_state: dict[str, int] = {}
        per_class: dict[str, int] = {}
        for a in self.agents.values():
            per_state[a.state.value] = per_state.get(a.state.value, 0) + 1
            per_class[a.klass] = per_class.get(a.klass, 0) + 1
        # The most recently sent messages, name-decorated, so the dashboard
        # shows live activity without the user opening the explorer.
        recent_messages = self.messaging.recent_messages(12)
        now = time.monotonic()
        # Lightweight balance peek — list_agents would re-serialize; use the
        # in-memory cache (kept in sync by the economy module).
        agent_balances = [
            {
                "agent_id": a.id,
                "name": a.name,
                "role": a.role,
                "class": a.klass,
                "office": a.office,
                "balance": round(a.bank_account, 2),
                "prestige": round(a.prestige, 1),
                "popularity": round(a.popularity, 1),
            }
            for a in self.agents.values()
        ]
        try:
            community_balance = self.store.get_community_balance()
        except Exception:
            community_balance = self.metrics.get("community_balance", 0.0)
        try:
            recent_transactions = self.store.list_transactions(limit=20)
        except Exception:
            recent_transactions = []
        return {
            "run_id": self.run_id,
            "paused": self.is_paused(),
            "tick_no": self._tick_no,
            "seconds_since_last_tick": round(now - self._last_tick_ts, 1),
            "seconds_since_last_activity": round(now - self._last_activity_ts, 1),
            "agents_per_state": per_state,
            "agents_per_class": per_class,
            "recent_messages": recent_messages,
            "active_vote": (
                self.voting.active.to_dict() if self.voting.active else None
            ),
            "broadcast_queue_length": len(self._broadcast_queue),
            "community_balance": round(community_balance, 2),
            "agent_balances": agent_balances,
            "recent_transactions": recent_transactions,
            "failure_reason": self.metrics.get("failure_reason"),
            "metrics": {
                "decisions_attempted": self.metrics["decisions_attempted"],
                "decisions_unparsed": self.metrics["decisions_unparsed"],
                "decisions_skipped": self.metrics["decisions_skipped"],
                "decisions_by_action": dict(self.metrics["decisions_by_action"]),
                "actions_applied": dict(self.metrics["actions_applied"]),
                "messages_sent": self.metrics["messages_sent"],
                "elections_held": self.metrics["elections_held"],
                "loans_requested": self.metrics["loans_requested"],
                "loans_granted": self.metrics["loans_granted"],
                "loans_denied": self.metrics["loans_denied"],
                "steals_attempted": self.metrics["steals_attempted"],
                "steals_succeeded": self.metrics["steals_succeeded"],
                "gifts_made": self.metrics["gifts_made"],
                "sabotages_attempted": self.metrics["sabotages_attempted"],
                "sabotages_succeeded": self.metrics["sabotages_succeeded"],
                "influence_actions": self.metrics["influence_actions"],
                "office_powers_used": self.metrics["office_powers_used"],
                "community_balance": self.metrics["community_balance"],
                "failure_reason": self.metrics["failure_reason"],
                "inference_errors": self.metrics["inference_errors"],
                "last_error": self.metrics["last_error"],
                "last_error_ts": self.metrics["last_error_ts"],
                "last_skip_reason": self.metrics["last_skip_reason"],
                # Last decisions-batch aggregates + live in-flight gauges. The
                # live gauges are sampled NOW, so they update mid-batch (the
                # /state poll runs on the same loop as the tick).
                "inference": self.metrics.get("inference", {}),
                "inference_live": self.inference.inflight_snapshot(),
            },
        }

    def diagnostics(self) -> dict:
        """Detail dump for /diag — includes the last raw LLM response."""
        return {
            "run_id": self.run_id,
            "tick_no": self._tick_no,
            "metrics": dict(self.metrics),
            "inference_endpoint": self.cfg.inference.endpoint,
            "inference_model": self.cfg.inference.model,
            "inference_backend": self.cfg.inference.backend,
        }
