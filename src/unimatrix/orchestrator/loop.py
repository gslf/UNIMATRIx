"""Main asyncio orchestrator.

Owns the world: agents, conversations, votes, broadcast queue, registry row.

Tick (every cfg.simulation.tick_interval_seconds):
  1. Decay social_need.
  2. If a vote is active → collect & close it (single batched call).
  3. Else, drain the broadcast queue (1 broadcast per tick).
  4. Else, run idle-agent decisions in parallel (batched), then process them
     in declaration order so we don't double-book targets.
  5. Run a turn for each active conversation in parallel.
  6. Anti-silence: if quiet for too long, force-trigger interactions.
  7. Auto-checkpoint on its own cadence.

Concurrency: all LLM calls are awaited inside `asyncio.gather` so vLLM can
batch them server-side; the inference client also caps in-flight requests
with a semaphore.
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
from ..conversations import ConversationEngine
from ..inference import GenerationRequest, InferenceClient
from ..inference.client import parse_json_lenient
from ..memory import MemoryManager
from ..persistence import Registry, RunStore, utc_now_iso
from ..voting import VotingModule
from . import silence, social_need


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
        self.conv = ConversationEngine(
            cfg, store, memory, inference, self.prompts, self.agents, self.console
        )
        self.voting = VotingModule(
            cfg, store, memory, inference, self.prompts, self.agents, self.console
        )
        self._broadcast_queue: list[BroadcastItem] = []
        self._paused = asyncio.Event()
        self._paused.set()  # set means NOT paused; clear() to pause
        self._stop = asyncio.Event()
        self._last_tick_ts = time.monotonic()
        self._last_activity_ts = time.monotonic()
        self._last_checkpoint_ts = time.monotonic()
        self._rng = random.Random(cfg.simulation.seed)
        self._tick_no = 0
        # Tick number at which the most recent vote was opened. Drives the
        # periodic-vote forcing in _run_idle_decisions.
        self._last_vote_tick: int = 0
        self.metrics: dict = {
            "decisions_attempted": 0,
            "decisions_unparsed": 0,
            "decisions_skipped": 0,        # rejected at _apply_decision (bad target, etc.)
            "decisions_by_action": {},     # action -> count (what the LLM picked)
            "actions_applied": {},          # action -> count (what actually went through)
            "conversations_opened": 0,
            "votes_proposed": 0,
            "votes_completed": 0,
            "votes_approved": 0,
            "votes_rejected": 0,
            "votes_null_total": 0,        # cumulative malformed-after-retries votes
            "forced_vote_failures": 0,     # forced propose_vote that didn't apply
            "broadcasts_sent": 0,
            "inference_errors": 0,
            "last_error": None,
            "last_error_ts": None,
            "last_decision_raw": None,     # last raw LLM response (truncated)
            "last_skip_reason": None,
        }

    # ----- bootstrap -----

    async def initialize(self) -> None:
        # seed agent runtime objects from spec, persist initial state
        for spec in self.cfg.agents:
            ag = Agent.from_spec(spec, self.cfg.social.social_need_initial)
            self.agents[ag.id] = ag
            await asyncio.to_thread(self.store.upsert_agent, ag.to_db_row())
            await asyncio.to_thread(
                self.store.record_status_change, ag.id, "initial", None, ag.role
            )
            await asyncio.to_thread(
                self.store.record_status_change, ag.id, "initial", None, ag.klass
            )

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
            self.registry.update_status(self.run_id, "ended", utc_now_iso())

    async def _on_pause_entered(self) -> None:
        self.console.log("[bold red]paused[/]; closing active conversations")
        await self.conv.close_all(end_reason="paused")
        await self._write_checkpoint()
        await asyncio.to_thread(
            self.store.record_event, "pause", {"ts": utc_now_iso()}
        )
        self.registry.update_status(self.run_id, "paused")

    async def _tick(self) -> None:
        self._tick_no += 1
        now = time.monotonic()
        elapsed = now - self._last_tick_ts
        self._last_tick_ts = now

        # 1) decay
        social_need.decay(
            self.agents.values(), elapsed, self.cfg.social.social_need_decay_per_minute
        )

        # 2) recovery path: a vote left active from a prior tick (e.g. crash
        # mid-vote) — resolve it before doing anything else.
        if self.voting.is_active():
            await self._resolve_active_vote()
            return

        # 3) drain broadcast queue (one per tick) — non-interruptive: active
        # conversations keep going, with the broadcast spliced into their
        # history.
        if self._broadcast_queue:
            item = self._broadcast_queue.pop(0)
            try:
                await self._apply_broadcast(item)
            except Exception as exc:
                self.console.log(f"[red]broadcast failed[/]: {exc}")
            self._last_activity_ts = time.monotonic()
            # fall through: still let conversations advance this tick

        # 4) idle agents decide. If any agent's decision opens a vote, the
        # society pauses immediately: _apply_decision() suspends conversations
        # and the vote runs in-tick right after the decision batch settles.
        await self._run_idle_decisions()
        if self.voting.is_active():
            # Decisions are batched upfront, so all in-flight generation from
            # this tick's decision pass has already settled. Conversations
            # were suspended before they could advance this tick. Run the
            # vote now and resume.
            await self._resolve_active_vote()
            return

        # 5) advance active conversations — multiple turns per tick so dialogue
        # actually flows instead of crawling at one message every several seconds.
        for _ in range(max(1, self.cfg.conversation.turns_per_tick)):
            if not self.conv.active:
                break
            results = await asyncio.gather(
                *(self.conv.take_turn(c) for c in list(self.conv.active.values())),
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, Exception):
                    self.console.log(f"[red]conversation turn failed[/]: {r}")
            self._last_activity_ts = time.monotonic()

        # 6) anti-silence
        await self._maybe_break_silence()

    async def _resolve_active_vote(self) -> None:
        """Run debate + tally for the currently-open proposal, then resume.

        Called both as a recovery path at tick start (if a vote was left open
        across ticks) and inline at the end of the decisions phase when a
        proposal is opened in the current tick.
        """
        try:
            # Pre-vote debate (idempotent — guarded by proposal.debated).
            # With voting.debate_rounds=0 this is a no-op.
            await self.voting.run_debate()
            result = await self.voting.collect_and_close()
            self.metrics["votes_completed"] += 1
            self.metrics["votes_null_total"] += int(result.get("null", 0))
            if result.get("outcome") == "approved":
                self.metrics["votes_approved"] += 1
            else:
                self.metrics["votes_rejected"] += 1
        except Exception as exc:
            self.console.log(f"[red]vote round failed[/]: {exc}")
            # Don't get stuck on a vote we can't run — clear it so the
            # simulation moves on instead of looping the same failure.
            await self.voting.abort_active()
        await self.conv.resume_all()
        self._last_activity_ts = time.monotonic()

    # ----- idle decisions -----

    async def _run_idle_decisions(self) -> None:
        idle_agents = [
            a for a in self.agents.values() if a.state == AgentState.IDLE
        ]
        if not idle_agents:
            return
        # Iterate in randomized order each tick (deterministic per-seed) so
        # nobody is permanently first.
        self._rng.shuffle(idle_agents)
        # Optional throttle: cap how many agents we ask the LLM about per tick.
        # When set, prefer the agents who most need to interact (lowest
        # social_need) — they'll be the ones forced to act anyway.
        cap = self.cfg.social.max_idle_decisions_per_tick
        if cap and len(idle_agents) > cap:
            idle_agents.sort(key=lambda a: a.social_need)
            idle_agents = idle_agents[:cap]

        # Build per-agent prompt
        recent_events = [
            f"{e['event_type']}: {json.dumps(e['payload'])[:120]}"
            for e in await asyncio.to_thread(self.store.recent_events, 10)
        ]
        peers = list(self.agents.values())
        open_groups = [
            c.id for c in self.conv.active.values() if c.type == "group"
        ]

        # Startup grace: for the first `warmup_ticks` ticks no vote (forced or
        # spontaneous) may happen — society warms up first.
        in_warmup = self._tick_no <= self.cfg.voting.warmup_ticks

        # Periodic vote forcing: if N ticks have passed without any vote, pick
        # one idle agent at random and force their next decision to be
        # propose_vote. The agent still picks the target / change_type / value
        # via LLM, so the proposal stays motivated.
        ticks_since_vote = self._tick_no - self._last_vote_tick
        must_force_vote = (
            not in_warmup
            and not self.voting.is_active()
            and ticks_since_vote >= self.cfg.voting.max_ticks_without_vote
            and bool(idle_agents)
        )
        forced_vote_id: str | None = None
        if must_force_vote:
            forced_vote_id = self._rng.choice(idle_agents).id
            self.console.log(
                f"[yellow]forcing periodic vote[/]: {ticks_since_vote} "
                f"ticks since last; {forced_vote_id} must propose"
            )

        # Forced action when below threshold
        prompts_payload: list[tuple[Agent, GenerationRequest]] = []
        for a in idle_agents:
            forced = a.social_need < self.cfg.social.social_need_critical_threshold
            force_vote_for_this = (a.id == forced_vote_id)
            medium = await self.memory.medium_term(a.id)
            long_term = [
                r.text
                for r in await self.memory.long_term(a.id, "what should I do now")
            ]
            msgs, stub_ctx = self.prompts.decide_action_messages(
                a, peers, medium, long_term, recent_events, forced, open_groups,
                force_vote=force_vote_for_this,
                ticks_since_vote=ticks_since_vote,
                voting_disabled=in_warmup,
            )
            prompts_payload.append(
                (
                    a,
                    GenerationRequest(
                        messages=msgs,
                        max_tokens=self.cfg.inference.max_tokens_per_decision,
                        json_mode=True,
                        stub_kind="decision",
                        stub_context=stub_ctx,
                    ),
                )
            )

        try:
            raws = await self.inference.generate_batch(
                [r for _, r in prompts_payload]
            )
        except Exception as exc:
            # Don't kill the loop on a transient backend hiccup. Log and skip
            # this tick's decisions; conversations and timers keep running.
            self.metrics["inference_errors"] += 1
            self.metrics["last_error"] = str(exc)
            self.metrics["last_error_ts"] = utc_now_iso()
            self.console.log(
                f"[red]idle-decisions batch failed[/], skipping tick: {exc}"
            )
            return
        decisions: list[tuple[Agent, dict]] = []
        for (a, _), raw in zip(prompts_payload, raws):
            self.metrics["decisions_attempted"] += 1
            self.metrics["last_decision_raw"] = (raw or "")[:600]
            parsed = parse_json_lenient(raw, want_keys=("action",))
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
        # Apply decisions sequentially so we don't try to enroll the same target twice.
        votes_proposed_before = self.metrics["votes_proposed"]
        for ag, dec in decisions:
            if ag.state != AgentState.IDLE:
                continue
            try:
                await self._apply_decision(ag, dec)
            except Exception as exc:  # do not let one bad decision kill the loop
                self.console.log(f"[red]decision error for {ag.id}[/]: {exc}")
        # If we forced a vote and nothing actually got proposed this tick,
        # log it. _last_vote_tick is intentionally NOT updated here, so the
        # forcing repeats next tick.
        if forced_vote_id is not None and (
            self.metrics["votes_proposed"] == votes_proposed_before
        ):
            self.metrics["forced_vote_failures"] += 1
            self.console.log(
                f"[red]forced vote failed[/] for {forced_vote_id} "
                f"(will retry next tick)"
            )

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

    def _normalize_proposal(
        self,
        target: Agent,
        change_type_raw: object,
        to_value_raw: object,
    ) -> tuple[str | None, str, str]:
        """Make a best-effort repair of an LLM-emitted proposal.

        Handles: bad case ("Minister" → "minister"), fuzzy role/class names,
        missing change_type (inferred from to_value), and no-op proposals
        (auto-pick a different value of the same kind).
        Returns (change_type, to_value, note). change_type is None if we
        couldn't repair the proposal at all.
        """
        role_ids = {r.id.lower(): r.id for r in self.cfg.roles}
        role_names = {r.name.lower(): r.id for r in self.cfg.roles}
        class_ids = {c.lower(): c for c in self.cfg.classes}
        ct_raw = (str(change_type_raw or "")).strip().lower()
        tv_raw = (str(to_value_raw or "")).strip()
        tv_lc = tv_raw.lower()
        # Resolve to_value as a role or a class via id or name.
        as_role = role_ids.get(tv_lc) or role_names.get(tv_lc)
        as_class = class_ids.get(tv_lc)
        if not as_role and tv_lc:
            for k, v in role_ids.items():
                if tv_lc in k or k in tv_lc:
                    as_role = v
                    break
        if not as_class and tv_lc:
            for k, v in class_ids.items():
                if tv_lc in k or k in tv_lc:
                    as_class = v
                    break
        # Infer change_type if missing or wrong.
        if ct_raw not in ("role", "class"):
            if as_role and not as_class:
                change_type = "role"
            elif as_class and not as_role:
                change_type = "class"
            else:
                return None, "", f"unknown change_type={change_type_raw!r}, to_value={to_value_raw!r}"
        else:
            change_type = ct_raw
        # Pick the resolved value matching change_type.
        if change_type == "role":
            to_value = as_role or ""
        else:
            to_value = as_class or ""
        if not to_value:
            return None, "", f"could not resolve to_value={to_value_raw!r} as a {change_type}"
        # No-op repair: if the target already has this value, pick the next
        # different one of the same kind (any change is better than none —
        # the agent wanted SOMETHING to shift).
        current = target.role if change_type == "role" else target.klass
        note = ""
        if to_value == current:
            if change_type == "role":
                pool = [r.id for r in self.cfg.roles if r.id != current]
            else:
                pool = [c for c in self.cfg.classes if c != current]
            if not pool:
                return None, "", "no-op proposal and no alternatives exist"
            new_to = self._rng.choice(pool)
            note = (
                f"target {target.id} already has {change_type}={current!r}; "
                f"shifting proposal to {new_to!r}"
            )
            to_value = new_to
        return change_type, to_value, note

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
        if action == "start_1to1":
            tgt = self._resolve_agent_ref(dec.get("target"))
            if not tgt or tgt.id == ag.id:
                self._skip("start_1to1", f"{ag.id}: target={dec.get('target')!r} not resolved")
                return
            topic = str(dec.get("topic") or "").strip()
            conv = await self.conv.open_1to1(ag, tgt, topic)
            if conv is None:
                self._skip("start_1to1", f"{ag.id}: target {tgt.id} unavailable (busy/voting/cooldown)")
                return
            self.metrics["conversations_opened"] += 1
            self._applied(action)
            return
        if action == "start_group":
            refs = dec.get("targets") or []
            tgts: list[Agent] = []
            for r in refs:
                t = self._resolve_agent_ref(r)
                if t and t.id != ag.id and t not in tgts:
                    tgts.append(t)
            if len(tgts) < 2:
                self._skip("start_group", f"{ag.id}: only {len(tgts)} targets resolved (need ≥2)")
                return
            topic = str(dec.get("topic") or "").strip()
            conv = await self.conv.open_group(ag, tgts, topic)
            if conv is None:
                self._skip("start_group", f"{ag.id}: group could not be assembled")
                return
            self.metrics["conversations_opened"] += 1
            self._applied(action)
            return
        if action == "broadcast":
            msg = str(dec.get("message") or "").strip()
            if not msg:
                self._skip("broadcast", f"{ag.id}: empty message")
                return
            await self.queue_broadcast(ag.id, msg)
            self.metrics["broadcasts_sent"] += 1
            self._applied(action)
            return
        if action == "join_group":
            cid = dec.get("conversation_id")
            try:
                cid_i = int(cid) if cid is not None else None
            except (TypeError, ValueError):
                self._skip("join_group", f"{ag.id}: bad conversation_id={cid!r}")
                return
            if cid_i is None:
                self._skip("join_group", f"{ag.id}: missing conversation_id")
                return
            ok = await self.conv.join_group(ag, cid_i)
            if ok:
                self._applied(action)
            else:
                self._skip("join_group", f"{ag.id}: cannot join conv {cid_i}")
            return
        if action == "propose_vote":
            if self._tick_no <= self.cfg.voting.warmup_ticks:
                self._skip(
                    "propose_vote",
                    f"{ag.id}: blocked during startup warmup "
                    f"(tick {self._tick_no} ≤ warmup {self.cfg.voting.warmup_ticks})",
                )
                return
            p = dec.get("proposal") or {}
            tgt = self._resolve_agent_ref(p.get("target"))
            if not tgt:
                self._skip("propose_vote", f"{ag.id}: target={p.get('target')!r} not resolved")
                return
            # Repair common LLM mistakes (case, fuzzy names, missing
            # change_type, no-op targeting). When a vote is proposed, we want
            # it to actually run — only outright nonsense gets rejected here.
            change_type, to_value, repair_note = self._normalize_proposal(
                tgt, p.get("change_type"), p.get("to_value")
            )
            if change_type is None:
                self._skip("propose_vote", f"{ag.id}: {repair_note}")
                return
            motivation = str(p.get("motivation") or "").strip()
            proposal = await self.voting.open(
                proposer_id=ag.id,
                target_id=tgt.id,
                change_type=change_type,
                to_value=to_value,
                motivation=motivation,
            )
            if proposal is None:
                self._skip("propose_vote", f"{ag.id}: proposal rejected (invalid or vote in progress)")
                return
            self.metrics["votes_proposed"] += 1
            self._last_vote_tick = self._tick_no
            if repair_note:
                self.console.log(f"[dim]proposal repaired[/]: {repair_note}")
            self._applied(action)
            if self.voting.is_active():
                # Pause ongoing conversations so the vote round can run.
                # They are restored after the tally in _tick().
                await self.conv.suspend_all("vote")
            return
        # unknown action
        self._skip(action, f"{ag.id}: unknown action")

    @staticmethod
    def _summarize_decision(dec: dict) -> str:
        action = dec.get("action")
        if action == "start_1to1":
            return f"→ {dec.get('target')!r} : {str(dec.get('topic',''))[:60]!r}"
        if action == "start_group":
            return f"→ {dec.get('targets')} : {str(dec.get('topic',''))[:60]!r}"
        if action == "broadcast":
            return f": {str(dec.get('message',''))[:80]!r}"
        if action == "join_group":
            return f"#{dec.get('conversation_id')}"
        if action == "propose_vote":
            p = dec.get("proposal") or {}
            motiv = str(p.get("motivation") or "").strip()
            tail = f" ({motiv[:60]!r})" if motiv else ""
            return (
                f"{p.get('change_type')} {p.get('target')!r} → "
                f"{p.get('to_value')!r}{tail}"
            )
        return ""

    # ----- broadcasts -----

    async def _apply_broadcast(self, item: BroadcastItem) -> None:
        # Broadcasts are public events but they MUST NOT kill ongoing
        # conversations. Idle agents flicker through listening_broadcast for a
        # tick (so the UI shows attention); conversation participants stay in
        # their dialogue and get the broadcast spliced into their history.
        sender = self.agents.get(item.sender_id)
        sender_label = sender.name if sender else (
            "the public crier" if item.sender_id == "HUMAN" else item.sender_id
        )
        idle_listeners = [a for a in self.agents.values() if a.state == AgentState.IDLE]
        for a in idle_listeners:
            a.state = AgentState.LISTENING_BROADCAST
            await asyncio.to_thread(
                self.store.update_agent_state, a.id, state=a.state.value
            )
        event_type = "broadcast_human" if item.sender_id == "HUMAN" else "broadcast_agent"
        eid = await asyncio.to_thread(
            self.store.record_event,
            event_type,
            {"sender_id": item.sender_id, "message": item.message},
        )
        # social_need gain is half (listeners). Conversation participants gain
        # the full amount each turn anyway, so skip them here.
        gain = self.cfg.social.social_need_gain_per_turn / 2.0
        for a in idle_listeners:
            a.social_need = min(100.0, a.social_need + gain)
            await asyncio.to_thread(
                self.store.update_agent_state, a.id, social_need=a.social_need
            )
        await self.memory.add_public_event(
            eid,
            list(self.agents.keys()),
            f"Broadcast from {sender_label}: {item.message}",
        )
        # Splice into in-progress conversations so participants can react.
        self.conv.inject_external_line(
            f"[BROADCAST from {sender_label}] {item.message}"
        )
        # Return idle listeners to idle.
        for a in idle_listeners:
            a.state = AgentState.IDLE
            await asyncio.to_thread(
                self.store.update_agent_state, a.id, state=a.state.value
            )
        self.console.log(
            f"[bold green]BROADCAST[/] from {sender_label}: {item.message[:80]}"
        )

    # ----- anti-silence -----

    async def _maybe_break_silence(self) -> None:
        if self.conv.active or self.voting.is_active() or self._broadcast_queue:
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
        # treated as forced in `_run_idle_decisions`).
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
                    "current_conversation_id": a.current_conversation_id,
                }
                for a in self.agents.values()
            ],
            "broadcast_queue": [
                {"sender_id": b.sender_id, "message": b.message}
                for b in self._broadcast_queue
            ],
            "active_vote": (
                self.voting.active.to_dict() if self.voting.active else None
            ),
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
        # Decorate active conversations with names + the most recent message so
        # the dashboard shows live activity without the user having to open the
        # explorer.
        active_convs = []
        for c in self.conv.active.values():
            participants = [
                self.agents[i].name for i in c.participant_ids if i in self.agents
            ]
            last_speaker, last_text = (None, None)
            if c.history:
                last_speaker, last_text = c.history[-1]
            active_convs.append({
                "id": c.id,
                "type": c.type,
                "participants": c.participant_ids,
                "participant_names": participants,
                "turn_index": c.turn_index,
                "last_speaker": last_speaker,
                "last_text": last_text,
            })
        now = time.monotonic()
        return {
            "run_id": self.run_id,
            "paused": self.is_paused(),
            "tick_no": self._tick_no,
            "seconds_since_last_tick": round(now - self._last_tick_ts, 1),
            "seconds_since_last_activity": round(now - self._last_activity_ts, 1),
            "agents_per_state": per_state,
            "agents_per_class": per_class,
            "active_conversations": active_convs,
            "active_vote": (
                self.voting.active.to_dict() if self.voting.active else None
            ),
            "broadcast_queue_length": len(self._broadcast_queue),
            "metrics": {
                "decisions_attempted": self.metrics["decisions_attempted"],
                "decisions_unparsed": self.metrics["decisions_unparsed"],
                "decisions_skipped": self.metrics["decisions_skipped"],
                "decisions_by_action": dict(self.metrics["decisions_by_action"]),
                "actions_applied": dict(self.metrics["actions_applied"]),
                "conversations_opened": self.metrics["conversations_opened"],
                "votes_proposed": self.metrics["votes_proposed"],
                "votes_completed": self.metrics["votes_completed"],
                "votes_approved": self.metrics["votes_approved"],
                "votes_rejected": self.metrics["votes_rejected"],
                "votes_null_total": self.metrics["votes_null_total"],
                "broadcasts_sent": self.metrics["broadcasts_sent"],
                "inference_errors": self.metrics["inference_errors"],
                "last_error": self.metrics["last_error"],
                "last_error_ts": self.metrics["last_error_ts"],
                "last_skip_reason": self.metrics["last_skip_reason"],
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
