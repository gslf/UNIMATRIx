"""Main asyncio orchestrator.

Owns the world: agents, messaging, the broadcast queue, the registry row.

There is no imposed political-economic ladder (no prestige, class, election,
office, salary). The only law the engine enforces is FINITUDE: each agent's
vitality depletes every tick and, at zero, the agent dies. Everything social is
left to emerge from the agents' own speech and self-construction.

Tick (every cfg.simulation.tick_interval_seconds):
  1. Vitality decays; any agent that hits the death threshold dies (and stops
     acting forever).
  2. Drain the broadcast queue (1 broadcast/tick → every living agent's inbox).
  3. Decision pass: each living, idle agent perceives its world and decides what
     to say. Messages are delivered NEXT tick (single swap site).
  4. Self-revision pass (staggered): some agents rewrite their evolving
     self-model from lived experience — the engine of identity evolution.
  5. Anti-silence: if the world has gone quiet too long, nudge a few agents.
  6. Auto-checkpoint on its own cadence.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from ..agents import Agent, AgentState, PromptBuilder
from ..config import Config
from ..messaging import MessagingModule
from ..inference import GenerationRequest, InferenceClient
from ..inference.client import parse_json_lenient
from ..memory import MemoryManager
from ..persistence import Registry, RunStore, utc_now_iso
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
        self._rng = random.Random(cfg.simulation.seed)
        self.messaging = MessagingModule(
            cfg, store, memory, inference, self.prompts, self.agents, self.console
        )
        self._broadcast_queue: list[BroadcastItem] = []
        self._paused = asyncio.Event()
        self._paused.set()  # set means NOT paused; clear() to pause
        self._stop = asyncio.Event()
        self._last_tick_ts = time.monotonic()
        self._last_activity_ts = time.monotonic()
        self._last_checkpoint_ts = time.monotonic()
        self._tick_no = 0
        # staggered self-revision bookkeeping + successor id sequence
        self._last_revision_tick: dict[str, int] = {}
        self._summaries_at_last_revision: dict[str, int] = {}
        self._birth_seq = 0
        self.metrics: dict = {
            "decisions_attempted": 0,
            "decisions_unparsed": 0,
            "messages_sent": 0,
            "self_revisions": 0,
            "deaths": 0,
            "living": 0,
            "projects_started": 0,
            "projects_completed": 0,
            "work_actions": 0,
            "artifacts_authored": 0,
            "adoptions": 0,
            "bonds_formed": 0,
            "groups_founded": 0,
            "shares": 0,
            "births": 0,
            "inference_errors": 0,
            "last_error": None,
            "last_error_ts": None,
            "last_decision_raw": None,
            "failure_reason": None,
            "inference": {},
        }

    def _bump(self, key: str, n: int = 1) -> None:
        self.metrics[key] = self.metrics.get(key, 0) + n

    # ----- bootstrap -----

    async def initialize(self) -> None:
        """Bring a brand-new world into existence from thin seeds."""
        w = self.cfg.world
        for spec in self.cfg.agents:
            ag = Agent.from_spec(
                spec,
                self.cfg.social.social_need_initial,
                vitality_initial=w.vitality_initial,
                sustenance_initial=w.sustenance_initial,
                blank_slate=w.blank_slate,
            )
            self.agents[ag.id] = ag

        for ag in self.agents.values():
            await asyncio.to_thread(self.store.upsert_agent, ag.to_db_row())
            # v0 of the self-model: the origin of the 'becoming' timeline.
            await asyncio.to_thread(
                self.store.add_self_model, ag.id, 0, 0, ag.self_model, "born"
            )

    async def initialize_from_existing(self) -> None:
        """Re-attach to a previously-paused run. Agents are reconstructed from
        cfg.agents (for the seed) and overlaid with their stored runtime state
        (self-model, vitality, alive, social_need)."""
        rows_by_id = {
            r["agent_id"]: r
            for r in await asyncio.to_thread(self.store.list_agents)
        }
        w = self.cfg.world
        # First, reconstruct seed-configured agents.
        for spec in self.cfg.agents:
            ag = Agent.from_spec(
                spec,
                self.cfg.social.social_need_initial,
                vitality_initial=w.vitality_initial,
                sustenance_initial=w.sustenance_initial,
                blank_slate=w.blank_slate,
            )
            self.agents[ag.id] = ag
        # Then overlay every persisted agent (including successors born mid-run,
        # who are not in the config) with their stored state.
        for aid, row in rows_by_id.items():
            ag = self.agents.get(aid)
            if ag is None:
                ag = Agent(
                    id=aid, name=row.get("name") or aid,
                    gender=row.get("gender") or "x",
                    circumstance=row.get("circumstance") or "",
                    disposition=row.get("disposition") or "",
                )
                self.agents[aid] = ag
            sm = row.get("self_model_json")
            if sm:
                try:
                    ag.self_model = json.loads(sm)
                except (TypeError, ValueError):
                    pass
            ag.self_model_version = int(row.get("self_model_version") or 0)
            v = row.get("vitality")
            if v is not None:
                ag.vitality = float(v)
            ag.alive = bool(row.get("alive", 1))
            su = row.get("sustenance")
            if su is not None:
                ag.sustenance = float(su)
            ag.born_tick = int(row.get("born_tick") or 0)
            try:
                ag.parent_ids = json.loads(row.get("parent_ids") or "[]")
            except (TypeError, ValueError):
                ag.parent_ids = []
            sn = row.get("social_need")
            if sn is not None:
                ag.social_need = float(sn)
            try:
                ag.state = AgentState(row.get("state") or "idle")
            except ValueError:
                ag.state = AgentState.IDLE
            if not ag.alive:
                ag.state = AgentState.DEAD

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
                            item.get("sender_id") or "HUMAN", str(item["message"])
                        )
                    )
            self._last_revision_tick = {
                k: int(v)
                for k, v in (ckpt.get("last_revision_tick") or {}).items()
            }
            try:
                self._birth_seq = int(ckpt.get("birth_seq") or 0)
            except (TypeError, ValueError):
                self._birth_seq = 0
            messaging_state = ckpt.get("messaging") or {}
        # The checkpoint counters can lag the persisted agents table (a crash can
        # lose ticks after the last auto-checkpoint). Seed the successor sequence
        # PAST any persisted `born_<seq>_<tick>` id so a resume never re-mints a
        # live agent's id and silently overwrites it.
        for aid in self.agents:
            if aid.startswith("born_"):
                try:
                    self._birth_seq = max(self._birth_seq, int(aid.split("_")[1]))
                except (IndexError, ValueError):
                    pass
        # Baseline the self-revision freshness counter to the current memory so a
        # resume doesn't treat all pre-resume memory as "fresh".
        if hasattr(self.memory, "count_summaries"):
            for aid in self.agents:
                self._summaries_at_last_revision[aid] = (
                    await self.memory.count_summaries(aid)
                )
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
        self._paused.set()

    async def queue_broadcast(self, sender_id: str, message: str) -> str:
        msg = (message or "").strip()
        if not msg:
            return "empty"
        self._broadcast_queue.append(BroadcastItem(sender_id, msg))
        return "scheduled"

    async def force_checkpoint(self) -> int:
        return await self._write_checkpoint()

    # ----- main loop -----

    async def run(self) -> None:
        try:
            while not self._stop.is_set():
                if not self._paused.is_set():
                    await self._on_pause_entered()
                    await self._paused.wait()
                    self._last_tick_ts = time.monotonic()
                await self._tick()
                if (
                    time.monotonic() - self._last_checkpoint_ts
                ) >= self.cfg.simulation.auto_checkpoint_minutes * 60:
                    await self._write_checkpoint()
                drift = time.monotonic() - self._last_tick_ts
                sleep_for = max(0.0, self.cfg.simulation.tick_interval_seconds - drift)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
                    break
                except asyncio.TimeoutError:
                    pass
        finally:
            if not self.metrics.get("failure_reason"):
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

        # 1) finitude: vitality decays; the spent die.
        await self._decay_and_resolve_deaths()

        living = [a for a in self.agents.values() if a.alive]
        if not living:
            await self._end_run_failed("everyone has died")
            return

        # social need decays for the living (drives reaching-out).
        social_need.decay(living, self.cfg.social.social_need_decay_per_tick)

        # 2) drain broadcast queue (one per tick).
        if self._broadcast_queue:
            item = self._broadcast_queue.pop(0)
            try:
                await self._apply_broadcast(item)
            except Exception as exc:
                self.console.log(f"[red]broadcast failed[/]: {exc}")
            self._last_activity_ts = time.monotonic()

        # 3) every living, idle agent decides what to say.
        await self._run_decisions()

        # 4) staggered self-revision — agents rewrite who they are.
        await self._run_self_revisions()

        # 5) anti-silence
        await self._maybe_break_silence()

    async def _end_run_failed(self, reason: str) -> None:
        self.metrics["failure_reason"] = reason
        self.console.log(f"[bold red]SIMULATION ENDED[/]: {reason}")
        await asyncio.to_thread(
            self.store.record_event,
            "simulation_failed",
            {"reason": reason, "tick": self._tick_no, "ts": utc_now_iso()},
        )
        try:
            self.registry.update_status(self.run_id, "ended", utc_now_iso())
        except Exception as exc:
            self.console.log(f"[red]could not mark registry ended[/]: {exc}")
        self.request_stop()

    # ----- finitude -----

    async def _decay_and_resolve_deaths(self) -> None:
        w = self.cfg.world
        for ag in self.agents.values():
            if not ag.alive:
                continue
            age = max(0, self._tick_no - ag.born_tick)
            ceiling = w.vitality_ceiling(age)
            ag.vitality -= w.vitality_decay_per_tick
            # Sustenance (from labor) is consumed to restore vitality — survival
            # depends on work. With no sustenance, vitality only falls.
            if ag.sustenance > 0 and w.sustenance_consumed_per_tick > 0:
                eat = min(ag.sustenance, w.sustenance_consumed_per_tick)
                ag.sustenance -= eat
                ag.vitality += eat * w.vitality_per_sustenance
            # Senescence: the attainable ceiling falls with age and hits 0 at
            # max_age_ticks, so even the well-fed eventually decline and die.
            if ag.vitality > ceiling:
                ag.vitality = ceiling
            # Food does not keep — hoarding is capped, so labor must be near-
            # continuous.
            if w.sustenance_max > 0 and ag.sustenance > w.sustenance_max:
                ag.sustenance = w.sustenance_max
            died_of_age = age >= w.max_age_ticks
            if died_of_age or ag.vitality <= w.vitality_death_threshold:
                cause = "age" if died_of_age else "vitality"
                ag.vitality = 0.0
                ag.alive = False
                ag.state = AgentState.DEAD
                self.metrics["deaths"] += 1
                await asyncio.to_thread(
                    self.store.update_agent_state,
                    ag.id, state=ag.state.value, vitality=0.0, alive=False,
                    sustenance=ag.sustenance,
                )
                legacy = (ag.self_model or {}).get("identity_narrative", "")
                await asyncio.to_thread(
                    self.store.record_death, ag.id, cause, self._tick_no,
                    legacy[:400],
                )
                await asyncio.to_thread(
                    self.store.record_event, "death",
                    {"agent_id": ag.id, "name": ag.name, "tick": self._tick_no,
                     "cause": cause, "legacy": legacy[:300]},
                )
                self.console.log(
                    f"[bold red]death[/] ({cause}): {ag.name} ({ag.id}) "
                    f"at tick {self._tick_no}"
                )
            else:
                await asyncio.to_thread(
                    self.store.update_agent_state, ag.id,
                    vitality=ag.vitality, sustenance=ag.sustenance,
                )

    # ----- per-tick decisions -----

    def _log_inference_window(self, n_calls: int, batch_s: float) -> None:
        stats = self.inference.window_stats()
        self.metrics["inference"] = {
            **stats, "batch_wall_s": round(batch_s, 1), "decisions": n_calls,
        }
        self.console.log(
            f"[dim]tick {self._tick_no}: {n_calls} decisions in {batch_s:.1f}s · "
            f"avg {stats['avg_latency_s']:.1f}s/call[/]"
        )

    async def _run_decisions(self) -> None:
        agents = [
            a for a in self.agents.values()
            if a.alive and a.state == AgentState.IDLE
        ]
        if not agents:
            return
        self._rng.shuffle(agents)
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
        peers = [a for a in self.agents.values() if a.alive]
        world_state = {
            "tick": self._tick_no,
            "artifacts": await asyncio.to_thread(self.store.recent_artifacts, 12),
            "projects": await asyncio.to_thread(self.store.active_projects),
            "groups": await asyncio.to_thread(self.store.list_groups),
        }

        prompts_payload: list[tuple[Agent, GenerationRequest]] = []
        for a in agents:
            forced = a.social_need < self.cfg.social.social_need_critical_threshold
            inbox = await self.messaging.read_inbox(a.id, self._tick_no)
            inbox_lines = self._render_inbox(inbox)
            query = inbox[-1].content if inbox else "what should I do now"
            medium = await self.memory.medium_term(a.id)
            long_term = [r.text for r in await self.memory.long_term(a.id, query)]
            rels = await asyncio.to_thread(self.store.relationships_for, a.id)
            msgs, stub_ctx = self.prompts.decide_action_messages(
                a, peers, medium, long_term, recent_events, forced, inbox_lines,
                world_state=world_state, relationships=rels,
            )
            stub_ctx["inbox_senders"] = [m.sender_id for m in inbox]
            prompts_payload.append(
                (a, GenerationRequest(
                    messages=msgs,
                    max_tokens=self.cfg.inference.max_tokens_per_decision,
                    json_mode=True,
                    stub_kind="decision",
                    stub_context=stub_ctx,
                    label=a.id,
                ))
            )

        self.inference.reset_window()
        _t0 = time.perf_counter()
        try:
            raws = await self.inference.generate_batch(
                [r for _, r in prompts_payload]
            )
        except Exception as exc:
            self.metrics["inference_errors"] += 1
            self.metrics["last_error"] = str(exc)
            self.metrics["last_error_ts"] = utc_now_iso()
            self.console.log(f"[red]decisions batch failed[/], skipping tick: {exc}")
            self.messaging.swap()
            return
        self._log_inference_window(len(prompts_payload), time.perf_counter() - _t0)

        decisions: list[tuple[Agent, dict]] = []
        for (a, _), raw in zip(prompts_payload, raws):
            self.metrics["decisions_attempted"] += 1
            self.metrics["last_decision_raw"] = (raw or "")[:600]
            parsed = parse_json_lenient(raw, want_keys=("messages",))
            if parsed is None:
                self.metrics["decisions_unparsed"] += 1
                parsed = {"messages": []}
            decisions.append((a, parsed))

        any_message = False
        for ag, dec in decisions:
            sent = await self._send_messages(ag, dec.get("messages"))
            any_message = any_message or sent

        # Apply at most one action per agent, sequentially (so a target is never
        # double-booked within a tick).
        for ag, dec in decisions:
            if not ag.alive:
                continue
            act = dec.get("action")
            if isinstance(act, dict) and act.get("verb"):
                try:
                    await self._apply_action(ag, act)
                except Exception as exc:
                    self.console.log(f"[red]action error for {ag.id}[/]: {exc}")

        if any_message:
            self._last_activity_ts = time.monotonic()
        self.messaging.swap()
        await self.messaging.run_reflections(self._tick_no)

    def _render_inbox(self, inbox: list) -> list[str]:
        lines: list[str] = []
        for m in inbox:
            sender = self.agents.get(m.sender_id)
            sender_name = sender.name if sender else (
                "a voice from beyond" if m.sender_id == "HUMAN" else m.sender_id
            )
            others = [r for r in m.recipient_ids if r != m.sender_id]
            also = f" (and {len(others) - 1} others)" if len(others) > 1 else ""
            tag = "[TO ALL] " if m.is_broadcast else ""
            lines.append(f"{tag}{sender_name}{also}: {m.content}")
        return lines

    async def _send_messages(self, ag: Agent, raw_messages) -> bool:
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
        if not ref:
            return None
        ref = str(ref).strip()
        for prefix in ("id:", "id="):
            if ref.lower().startswith(prefix):
                ref = ref[len(prefix):].strip()
        ref = ref.strip("[](){}<>\"' ")
        a = self.agents.get(ref)
        if a and a.alive:
            return a
        lc = ref.lower()
        for a in self.agents.values():
            if a.alive and a.name.lower() == lc:
                return a
        for a in self.agents.values():
            if a.alive and lc in a.name.lower():
                return a
        return None

    def _resolve_targets(self, refs, exclude_id: str, cap: int) -> list[Agent]:
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

    # ----- open action interpreter (labor / meaning / kinship / continuity) -----

    async def _apply_action(self, ag: Agent, act: dict) -> None:
        verb = str(act.get("verb") or "rest").strip().lower()
        if verb in ("rest", "do_nothing", ""):
            return
        if verb == "work":
            await self._do_work(ag, act)
        elif verb == "express":
            await self._do_express(ag, act)
        elif verb == "bond":
            await self._do_bond(ag, act)
        elif verb == "dissolve":
            await self._do_dissolve(ag, act)
        elif verb == "share":
            await self._do_share(ag, act)
        elif verb == "found_group":
            await self._do_found_group(ag, act)
        elif verb == "join_group":
            await self._do_join_group(ag, act)
        elif verb == "bear_successor":
            await self._do_bear_successor(ag, act)
        # unknown verbs are silently ignored (lenient by design)

    async def _do_work(self, ag: Agent, act: dict) -> None:
        w = self.cfg.world
        self._bump("work_actions")
        # Labor tires: working costs vitality, so it is a genuine trade-off
        # against the sustenance it eventually yields.
        if w.work_vitality_cost > 0 and ag.alive:
            ag.vitality -= w.work_vitality_cost
            await asyncio.to_thread(
                self.store.update_agent_state, ag.id, vitality=ag.vitality
            )
        try:
            pid = int(act.get("project") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            goal = str(act.get("goal") or "sustain ourselves").strip()[:200]
            pid = await asyncio.to_thread(
                self.store.create_project, ag.id, goal, "sustenance",
                w.project_default_target, self._tick_no,
            )
            self._bump("projects_started")
            await asyncio.to_thread(
                self.store.record_event, "project_started",
                {"project_id": pid, "founder_id": ag.id, "goal": goal},
            )
        proj = await asyncio.to_thread(
            self.store.add_project_effort,
            pid, ag.id, w.project_effort_per_work, self._tick_no,
        )
        if proj is None:
            return
        if proj["effort"] >= proj["target"] and proj["status"] == "active":
            await self._complete_project(proj)

    async def _complete_project(self, proj: dict) -> None:
        w = self.cfg.world
        pid = proj["id"]
        contributors = await asyncio.to_thread(self.store.project_contributors, pid)
        total = sum(contributors.values()) or 1.0
        output = f"sustenance ({w.project_sustenance_yield:g})"
        await asyncio.to_thread(
            self.store.complete_project, pid, output, self._tick_no
        )
        self._bump("projects_completed")
        for aid, eff in contributors.items():
            share = w.project_sustenance_yield * (eff / total)
            a = self.agents.get(aid)
            if a and a.alive:
                a.sustenance += share
                if w.sustenance_max > 0 and a.sustenance > w.sustenance_max:
                    a.sustenance = w.sustenance_max
                await asyncio.to_thread(
                    self.store.update_agent_state, aid, sustenance=a.sustenance
                )
                await self.memory.add_summary(
                    aid, f"I helped finish our shared work — {proj.get('goal')} — "
                    "and it sustained me.", tick_no=self._tick_no,
                )
        await asyncio.to_thread(
            self.store.record_event, "project_completed",
            {"project_id": pid, "goal": proj.get("goal"),
             "contributors": list(contributors.keys()),
             "yield": w.project_sustenance_yield},
        )
        self.console.log(
            f"[green]project completed[/]: #{pid} '{proj.get('goal')}' "
            f"-> {len(contributors)} contributor(s)"
        )

    async def _do_express(self, ag: Agent, act: dict) -> None:
        content = str(act.get("content") or "").strip()
        if not content:
            return
        kind = str(act.get("kind") or "belief").strip().lower()[:20]
        parent = act.get("remix")
        try:
            parent = int(parent) if parent not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            parent = None
        aid = await asyncio.to_thread(
            self.store.add_artifact, ag.id, kind, content[:600], self._tick_no, parent
        )
        self._bump("artifacts_authored")
        await asyncio.to_thread(
            self.store.record_event, "expression",
            {"artifact_id": aid, "author_id": ag.id, "kind": kind,
             "content": content[:200], "remix_of": parent},
        )
        # broadcast it softly into everyone's awareness via memory
        living_ids = [a.id for a in self.agents.values() if a.alive]
        await self.memory.add_public_event(
            aid + 10_000_000, living_ids,
            f"{ag.name} voiced a {kind} into the common world: {content[:160]}",
        )
        await self.memory.add_summary(
            ag.id, f"I voiced a {kind} into the common world: {content[:120]}",
            tick_no=self._tick_no,
        )
        self.console.log(f"[cyan]expression[/]: {ag.name} ({kind}) #{aid}")

    async def _do_bond(self, ag: Agent, act: dict) -> None:
        tgt = self._resolve_agent_ref(act.get("target"))
        if not tgt or tgt.id == ag.id:
            return
        type_ = str(act.get("type") or "friend").strip().lower()
        if type_ not in ("friend", "ally", "rival", "mentor", "partner", "kin"):
            type_ = "friend"
        note = str(act.get("note") or "").strip()[:160]
        await asyncio.to_thread(
            self.store.upsert_relationship,
            ag.id, tgt.id, type_, 1.0, note, self._tick_no,
        )
        self._bump("bonds_formed")
        await asyncio.to_thread(
            self.store.record_event, "bond",
            {"from_id": ag.id, "to_id": tgt.id, "type": type_, "note": note},
        )
        await self.memory.add_summary(
            ag.id, f"I bound myself to {tgt.name} as {type_}"
            + (f" — {note}" if note else "") + ".", tick_no=self._tick_no,
        )
        self.console.log(f"[magenta]bond[/]: {ag.name} -> {tgt.name} ({type_})")

    async def _do_dissolve(self, ag: Agent, act: dict) -> None:
        tgt = self._resolve_agent_ref(act.get("target"))
        if not tgt:
            return
        await asyncio.to_thread(
            self.store.dissolve_relationship, ag.id, tgt.id, self._tick_no
        )
        await asyncio.to_thread(
            self.store.record_event, "tie_dissolved",
            {"from_id": ag.id, "to_id": tgt.id},
        )

    async def _do_share(self, ag: Agent, act: dict) -> None:
        tgt = self._resolve_agent_ref(act.get("target"))
        if not tgt or tgt.id == ag.id or not tgt.alive:
            return
        try:
            amount = float(act.get("amount") or 0)
        except (TypeError, ValueError):
            return
        amount = min(max(0.0, amount), ag.sustenance)
        if amount <= 0:
            return
        ag.sustenance -= amount
        tgt.sustenance += amount
        await asyncio.to_thread(
            self.store.update_agent_state, ag.id, sustenance=ag.sustenance
        )
        await asyncio.to_thread(
            self.store.update_agent_state, tgt.id, sustenance=tgt.sustenance
        )
        # a gift strengthens the giver's tie to the receiver
        await asyncio.to_thread(
            self.store.upsert_relationship,
            ag.id, tgt.id, "friend", 0.5, "shared sustenance", self._tick_no,
        )
        self._bump("shares")
        await asyncio.to_thread(
            self.store.record_event, "share",
            {"from_id": ag.id, "to_id": tgt.id, "amount": round(amount, 1)},
        )
        await self.memory.add_summary(
            ag.id, f"I gave {amount:.0f} of my sustenance to {tgt.name}.",
            tick_no=self._tick_no,
        )
        self.console.log(
            f"[green]share[/]: {ag.name} gave {amount:.0f} sustenance to {tgt.name}"
        )

    async def _do_found_group(self, ag: Agent, act: dict) -> None:
        name = str(act.get("name") or "").strip()[:80]
        if not name:
            return
        purpose = str(act.get("purpose") or "").strip()[:200]
        gid = await asyncio.to_thread(
            self.store.create_group, name, ag.id, purpose, self._tick_no
        )
        self._bump("groups_founded")
        await asyncio.to_thread(
            self.store.record_event, "group_founded",
            {"group_id": gid, "name": name, "founder_id": ag.id, "purpose": purpose},
        )
        await self.memory.add_summary(
            ag.id, f"I gathered others into a collective I named {name}"
            + (f", to {purpose}" if purpose else "") + ".", tick_no=self._tick_no,
        )
        self.console.log(f"[blue]group founded[/]: '{name}' (#{gid}) by {ag.name}")

    async def _do_join_group(self, ag: Agent, act: dict) -> None:
        try:
            gid = int(act.get("group") or 0)
        except (TypeError, ValueError):
            return
        if gid <= 0:
            return
        ok = await asyncio.to_thread(
            self.store.join_group, gid, ag.id, self._tick_no
        )
        if ok:
            await asyncio.to_thread(
                self.store.record_event, "group_joined",
                {"group_id": gid, "agent_id": ag.id},
            )
            await self.memory.add_summary(
                ag.id, f"I joined a collective (#{gid}).", tick_no=self._tick_no,
            )

    async def _do_bear_successor(self, ag: Agent, act: dict) -> None:
        w = self.cfg.world
        living = sum(1 for a in self.agents.values() if a.alive)
        if living >= w.max_population:
            return
        partner = self._resolve_agent_ref(act.get("partner"))
        if not partner or partner.id == ag.id or not partner.alive:
            return
        # require a declared 'partner' tie of sufficient strength (matching the
        # action's stated precondition) and the resource cost.
        rel = await asyncio.to_thread(
            self.store.get_relationship, ag.id, partner.id
        )
        strength = float((rel or {}).get("strength") or 0)
        if not rel or rel.get("type") != "partner" \
                or strength < w.succession_min_partner_strength:
            return
        if ag.sustenance < w.succession_sustenance_cost:
            return
        ag.sustenance -= w.succession_sustenance_cost
        await asyncio.to_thread(
            self.store.update_agent_state, ag.id, sustenance=ag.sustenance
        )
        child = self._make_successor(ag, partner, act.get("name"))
        self.agents[child.id] = child
        await asyncio.to_thread(self.store.upsert_agent, child.to_db_row())
        await asyncio.to_thread(
            self.store.add_self_model, child.id, 0, self._tick_no,
            child.self_model, "born of succession",
        )
        inherited = {
            "from_parents": [ag.id, partner.id],
            "seed_self_model": child.self_model,
        }
        await asyncio.to_thread(
            self.store.record_lineage, child.id, [ag.id, partner.id],
            inherited, self._tick_no,
        )
        self._bump("births")
        await asyncio.to_thread(
            self.store.record_event, "birth",
            {"child_id": child.id, "name": child.name,
             "parents": [ag.id, partner.id], "tick": self._tick_no},
        )
        for parent, other in ((ag, partner), (partner, ag)):
            await self.memory.add_summary(
                parent.id, f"With {other.name}, I brought a new being into the "
                f"world: {child.name}. Something of me will outlast me.",
                tick_no=self._tick_no,
            )
        self.console.log(
            f"[bold green]birth[/]: {child.name} ({child.id}) "
            f"from {ag.name} + {partner.name}"
        )

    def _make_successor(self, p1: Agent, p2: Agent, name) -> Agent:
        """Create a successor whose seed self-model blends both parents' evolved
        identities + their cultures, so identity transmits across generations."""
        from ..agents import Agent as _Agent, default_self_model
        self._birth_seq += 1
        cid = f"born_{self._birth_seq}_{self._tick_no}"
        # Guard against ever colliding with an existing (incl. resumed) agent.
        while cid in self.agents:
            self._birth_seq += 1
            cid = f"born_{self._birth_seq}_{self._tick_no}"
        child_name = str(name).strip()[:40] if name else f"Child of {p1.name}"
        sm1, sm2 = p1.self_model or {}, p2.self_model or {}
        # blend: inherit a narrative seeded by both, merge values, take a few
        # beliefs from each, and an inherited goal.
        values = {}
        values.update(sm2.get("values") or {})
        values.update(sm1.get("values") or {})
        beliefs = list(dict.fromkeys(
            (sm1.get("beliefs") or [])[:2] + (sm2.get("beliefs") or [])[:2]
        ))
        narrative = (
            f"I was brought into being by {p1.name} and {p2.name}. I carry "
            "something of them, but who I become is mine to write."
        )
        child_sm = default_self_model(
            narrative,
            values=dict(list(values.items())[:5]),
            beliefs=beliefs,
            goals=(sm1.get("goals") or [])[:1],
            relationships_summary=f"Born to {p1.name} and {p2.name}.",
            mortality_stance="",
        )
        child = _Agent(
            id=cid,
            name=child_name,
            gender="x",
            circumstance=f"You were born into a world already populated, child "
                         f"of {p1.name} and {p2.name}.",
            disposition="",
            self_model=child_sm,
            self_model_version=0,
            vitality=self.cfg.world.vitality_initial,
            sustenance=self.cfg.world.sustenance_initial,
            born_tick=self._tick_no,
            parent_ids=[p1.id, p2.id],
            social_need=self.cfg.social.social_need_initial,
        )
        return child

    # ----- self-revision (the engine of self-construction) -----

    async def _run_self_revisions(self) -> None:
        """Staggered per-agent self-rewrite. An agent revises only if it is due
        (by a stable id-hash phase) and has accumulated fresh memories since its
        last revision."""
        w = self.cfg.world
        interval = w.self_revision_interval_ticks
        min_new = w.self_revision_min_new_memories
        due: list[Agent] = []
        for aid, ag in self.agents.items():
            if not ag.alive:
                continue
            if (self._tick_no + zlib.crc32(aid.encode())) % interval != 0:
                continue
            # Freshness gate: only revise if new lived experience (deed traces +
            # conversation reflections) has accumulated since the last revision.
            total = await self.memory.count_summaries(aid) \
                if hasattr(self.memory, "count_summaries") else 0
            fresh = total - self._summaries_at_last_revision.get(aid, 0)
            if min_new > 0 and fresh < min_new:
                continue
            due.append(ag)
        if not due:
            return

        recent_events = [
            f"{e['event_type']}: {json.dumps(e['payload'])[:400]}"
            for e in await asyncio.to_thread(self.store.recent_events, 8)
        ]
        commons = await asyncio.to_thread(self.store.recent_artifacts, 10)
        commons_by_id = {int(a["id"]): a for a in commons}
        reqs: list[GenerationRequest] = []
        for ag in due:
            medium = await self.memory.medium_term(ag.id)
            msgs, ctx = self.prompts.self_revision_messages(
                ag, medium, recent_events, commons=commons
            )
            reqs.append(GenerationRequest(
                messages=msgs,
                max_tokens=w.max_tokens_per_self_revision,
                json_mode=True,
                stub_kind="self_revision",
                stub_context=ctx,
                label=ag.id,
            ))
        try:
            raws = await self.inference.generate_batch(reqs)
        except Exception as exc:
            self.console.log(f"[red]self-revision batch failed[/]: {exc}")
            return
        done = 0
        for ag, raw in zip(due, raws):
            self._last_revision_tick[ag.id] = self._tick_no
            # mark this agent's experience as "consumed" regardless of outcome,
            # so a poison response is not retried every tick.
            if hasattr(self.memory, "count_summaries"):
                self._summaries_at_last_revision[ag.id] = (
                    await self.memory.count_summaries(ag.id)
                )
            try:
                parsed = parse_json_lenient(raw, want_keys=("identity_narrative",))
                if not isinstance(parsed, dict):
                    continue
                new_model = self._merge_self_model(ag.self_model, parsed)
                new_version = ag.self_model_version + 1
                # belief adoption from the commons (transmission + lineage)
                await self._apply_adoptions(
                    ag, parsed.get("adopt_ids"), commons_by_id, new_model, new_version
                )
                diff = self._self_model_diff(ag.self_model, new_model)
                ag.self_model = new_model
                ag.self_model_version = new_version
                self.metrics["self_revisions"] += 1
                done += 1
                await asyncio.to_thread(
                    self.store.update_agent_state,
                    ag.id,
                    self_model_json=ag.self_model,
                    self_model_version=ag.self_model_version,
                )
                await asyncio.to_thread(
                    self.store.add_self_model,
                    ag.id, ag.self_model_version, self._tick_no, ag.self_model, diff,
                )
                await asyncio.to_thread(
                    self.store.record_event, "self_revision",
                    {"agent_id": ag.id, "version": ag.self_model_version,
                     "tick": self._tick_no, "diff": diff[:300]},
                )
            except Exception as exc:  # one bad rewrite must not end the run
                self.console.log(f"[red]self-revision error for {ag.id}[/]: {exc}")
        self.console.log(f"[dim]self-revision: {done}/{len(due)} agent(s)[/]")

    async def _apply_adoptions(
        self, ag: Agent, adopt_ids, commons_by_id: dict, new_model: dict,
        new_version: int,
    ) -> None:
        """An agent embracing a commons idea: inject it as a belief and record
        the adoption (belief transmission + lineage)."""
        if not isinstance(adopt_ids, list):
            return
        beliefs = new_model.get("beliefs")
        if not isinstance(beliefs, list):  # defensive: tolerate a mangled field
            beliefs = [str(beliefs)] if beliefs not in (None, "", [], {}) else []
            new_model["beliefs"] = beliefs
        for raw_id in adopt_ids[:5]:
            try:
                art_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            art = commons_by_id.get(art_id)
            if not art or art.get("author_id") == ag.id:
                continue  # can't "adopt" your own, and unknown ids ignored
            belief = (art.get("content") or "").strip()[:200]
            if belief and belief not in beliefs:
                beliefs.append(belief)
            await asyncio.to_thread(
                self.store.record_adoption, ag.id, art_id, new_version, self._tick_no
            )
            self._bump("adoptions")
            await asyncio.to_thread(
                self.store.record_event, "adoption",
                {"agent_id": ag.id, "artifact_id": art_id,
                 "author_id": art.get("author_id"), "tick": self._tick_no},
            )

    @staticmethod
    def _merge_self_model(old: dict, new: dict) -> dict:
        """Accept the agent's rewrite, enforcing the canonical type of each field
        and falling back to prior values for anything the model left out or
        mangled. Live models routinely violate nested schemas (e.g. `beliefs` as
        a bare string), so we coerce at this boundary rather than crash later."""
        from ..agents import default_self_model
        merged = default_self_model()
        old = old or {}
        new = new if isinstance(new, dict) else {}
        for k, default in merged.items():
            val = new.get(k)
            ok = val not in (None, "", [], {})
            if ok and not isinstance(val, type(default)):
                # coerce to the field's canonical container type, else reject.
                if isinstance(default, list):
                    if isinstance(val, (str, int, float)):
                        val = [str(val)]
                    elif isinstance(val, (tuple, set)):
                        val = list(val)
                    else:
                        val = None
                elif isinstance(default, dict):
                    val = None  # a non-dict can't be trusted as `values`
                elif isinstance(default, str):
                    val = str(val)
                ok = val not in (None, "", [], {})
            merged[k] = val if ok else old.get(k, default)
        return merged

    @staticmethod
    def _self_model_diff(old: dict, new: dict) -> str:
        """A compact human-readable diff of what changed about who they are."""
        old = old or {}
        new = new or {}
        changes: list[str] = []
        for k in ("identity_narrative", "relationships_summary", "mortality_stance"):
            if (old.get(k) or "") != (new.get(k) or ""):
                changes.append(f"{k} changed")
        for k in ("beliefs", "goals"):
            o = set(map(str, old.get(k) or []))
            n = set(map(str, new.get(k) or []))
            added = n - o
            dropped = o - n
            if added:
                changes.append(f"+{k}: " + "; ".join(list(added)[:3]))
            if dropped:
                changes.append(f"-{k}: " + "; ".join(list(dropped)[:3]))
        ov = old.get("values") or {}
        nv = new.get("values") or {}
        if set(map(str, ov)) != set(map(str, nv)):
            changes.append("values shifted")
        return " | ".join(changes) if changes else "(reaffirmed)"

    # ----- broadcasts -----

    async def _apply_broadcast(self, item: BroadcastItem) -> None:
        sender = self.agents.get(item.sender_id)
        sender_label = sender.name if sender else (
            "a voice from beyond" if item.sender_id == "HUMAN" else item.sender_id
        )
        event_type = "broadcast_human" if item.sender_id == "HUMAN" else "broadcast_agent"
        eid = await asyncio.to_thread(
            self.store.record_event, event_type,
            {"sender_id": item.sender_id, "message": item.message},
        )
        living_ids = [a.id for a in self.agents.values() if a.alive]
        await self.memory.add_public_event(
            eid, living_ids, f"Heard by all from {sender_label}: {item.message}",
        )
        await self.messaging.deliver_broadcast(
            item.sender_id, f"[to all, from {sender_label}] {item.message}",
            self._tick_no,
        )
        self.console.log(
            f"[bold green]TO ALL[/] from {sender_label}: {item.message[:80]}"
        )

    # ----- anti-silence -----

    async def _maybe_break_silence(self) -> None:
        recent_message = self.messaging._last_message_tick == self._tick_no
        if recent_message or self._broadcast_queue:
            self._last_activity_ts = time.monotonic()
            return
        if (time.monotonic() - self._last_activity_ts) < self.cfg.social.silence_detection_seconds:
            return
        forced = silence.pick_forced_agents(
            [a for a in self.agents.values() if a.alive],
            self.cfg.social.forced_interaction_count_on_silence,
        )
        if not forced:
            return
        self.console.log(
            f"[yellow]anti-silence trigger[/]: nudging {len(forced)} agent(s)"
        )
        for a in forced:
            a.social_need = min(
                a.social_need, self.cfg.social.social_need_critical_threshold - 1.0
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
                    "vitality": a.vitality,
                    "sustenance": a.sustenance,
                    "alive": a.alive,
                    "self_model_version": a.self_model_version,
                }
                for a in self.agents.values()
            ],
            "broadcast_queue": [
                {"sender_id": b.sender_id, "message": b.message}
                for b in self._broadcast_queue
            ],
            "last_revision_tick": dict(self._last_revision_tick),
            "birth_seq": self._birth_seq,
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
        living = sum(1 for a in self.agents.values() if a.alive)
        self.metrics["living"] = living
        now = time.monotonic()
        return {
            "run_id": self.run_id,
            "paused": self.is_paused(),
            "tick_no": self._tick_no,
            "seconds_since_last_tick": round(now - self._last_tick_ts, 1),
            "seconds_since_last_activity": round(now - self._last_activity_ts, 1),
            "population": {"alive": living, "dead": len(self.agents) - living},
            "recent_messages": self.messaging.recent_messages(12),
            "broadcast_queue_length": len(self._broadcast_queue),
            "failure_reason": self.metrics.get("failure_reason"),
            "metrics": {
                "decisions_attempted": self.metrics["decisions_attempted"],
                "decisions_unparsed": self.metrics["decisions_unparsed"],
                "messages_sent": self.metrics["messages_sent"],
                "self_revisions": self.metrics["self_revisions"],
                "deaths": self.metrics["deaths"],
                "living": self.metrics["living"],
                "projects_started": self.metrics["projects_started"],
                "projects_completed": self.metrics["projects_completed"],
                "artifacts_authored": self.metrics["artifacts_authored"],
                "adoptions": self.metrics["adoptions"],
                "bonds_formed": self.metrics["bonds_formed"],
                "groups_founded": self.metrics["groups_founded"],
                "shares": self.metrics["shares"],
                "births": self.metrics["births"],
                "failure_reason": self.metrics["failure_reason"],
                "inference_errors": self.metrics["inference_errors"],
                "last_error": self.metrics["last_error"],
                "last_error_ts": self.metrics["last_error_ts"],
                "inference": self.metrics.get("inference", {}),
                "inference_live": self.inference.inflight_snapshot(),
            },
        }

    def diagnostics(self) -> dict:
        return {
            "run_id": self.run_id,
            "tick_no": self._tick_no,
            "metrics": dict(self.metrics),
            "inference_endpoint": self.cfg.inference.endpoint,
            "inference_model": self.cfg.inference.model,
            "inference_backend": self.cfg.inference.backend,
        }
