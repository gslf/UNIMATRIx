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
        # ecology: the spatial substrate — the grid of patches beings work
        self._patches: dict[tuple[int, int], dict] = {}
        self._harvest_intents: list[tuple[Agent, int, int]] = []
        self.metrics: dict = {
            "decisions_attempted": 0,
            "decisions_unparsed": 0,
            "messages_sent": 0,
            "self_revisions": 0,
            "deaths": 0,
            "living": 0,
            "cooperative_work": 0,
            "solo_work": 0,
            "mutual_aid_transfers": 0,
            "work_actions": 0,
            "artifacts_authored": 0,
            "adoptions": 0,
            "bonds_formed": 0,
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

        await self._init_ecology()

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
            try:
                stored_traits = json.loads(row.get("traits_json") or "{}")
                if isinstance(stored_traits, dict) and stored_traits:
                    ag.traits = {**ag.traits, **{k: float(v) for k, v in stored_traits.items()}}
            except (TypeError, ValueError):
                pass
            ag.pos_x = int(row.get("pos_x") or 0)
            ag.pos_y = int(row.get("pos_y") or 0)
            sn = row.get("social_need")
            if sn is not None:
                ag.social_need = float(sn)
            try:
                ag.state = AgentState(row.get("state") or "idle")
            except ValueError:
                ag.state = AgentState.IDLE
            if not ag.alive:
                ag.state = AgentState.DEAD

        await self._load_ecology()

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

        # 1.5) ecology: patches regrow toward capacity.
        await self._regen_patches()

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

        # 3.5) ecology: all who chose to work draw on their patch together, so
        # co-present crews out-earn lone workers (resolved as one batch).
        await self._resolve_harvests()

        # 4) staggered self-revision — agents rewrite who they are.
        await self._run_self_revisions()

        # 5) anti-silence
        await self._maybe_break_silence()

        # 6) periodically record the population's trait distribution — the
        # observable record of selection drifting the lineage over generations.
        if self._tick_no % 20 == 0:
            await self._record_trait_snapshot(living)

    async def _record_trait_snapshot(self, living: list[Agent]) -> None:
        from ..agents import default_traits
        keys = list(default_traits().keys())
        means = {
            k: round(sum(float(a.traits.get(k, 1.0)) for a in living) / len(living), 3)
            for k in keys
        } if living else {}
        max_gen = max((self._generation_depth(a) for a in living), default=0)
        await asyncio.to_thread(
            self.store.record_event, "trait_snapshot",
            {"tick": self._tick_no, "living": len(living),
             "trait_means": means, "max_generation": max_gen},
        )

    def _generation_depth(self, ag: Agent) -> int:
        """How many births separate a being from a founder (founders = 0)."""
        depth = 0
        seen: set[str] = set()
        cur = ag
        while cur is not None and cur.parent_ids and cur.id not in seen:
            seen.add(cur.id)
            cur = self.agents.get(cur.parent_ids[0])
            depth += 1
            if depth > 1000:  # cycle guard
                break
        return depth

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
            # metabolism is heritable: a frugal lineage decays slower and, all
            # else equal, lives and breeds more — selection acts on it.
            ag.vitality -= w.vitality_decay_per_tick * float(
                ag.traits.get("metabolism", 1.0)
            )
            # Sustenance (from labor) is consumed to restore vitality — survival
            # depends on work. With no sustenance, vitality only falls. But on a
            # "lean" tick (bad luck) the food does not nourish: the being keeps
            # its store yet gains nothing this moment. A run of lean ticks can
            # carry a loner toward death — the variance that mutual aid smooths.
            lean = w.bad_streak_prob > 0 and self._rng.random() < w.bad_streak_prob
            if ag.sustenance > 0 and w.sustenance_consumed_per_tick > 0 and not lean:
                eat = min(ag.sustenance, w.sustenance_consumed_per_tick)
                ag.sustenance -= eat
                ag.vitality += eat * w.vitality_per_sustenance
            # When a being is close to the edge, those it is bound to are drawn
            # on to pull it back — ties as insurance. This is automatic mutual
            # aid, not a rule the being chose; isolates have no one to draw on.
            if 0 < ag.vitality < w.pooling_rescue_threshold:
                await self._attempt_pool_rescue(ag, ceiling)
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

    async def _attempt_pool_rescue(self, ag: Agent, ceiling: float) -> None:
        """A being near death draws sustenance from those it is bound to (ties
        of sufficient strength), converting it to vitality on the spot. The
        givers spend real food, so this only helps those who built ties; a loner
        has no one to call on. Mutual aid, emerging from the bonds themselves."""
        w = self.cfg.world
        ties = await asyncio.to_thread(self.store.relationships_for, ag.id)
        need = w.pooling_max_transfer_per_tick
        if need <= 0:
            return
        pulled = 0.0
        for r in ties:
            if pulled >= need:
                break
            if float(r.get("strength") or 0) < w.pooling_min_tie_strength:
                continue
            donor = self.agents.get(r.get("subject_id"))
            if not donor or not donor.alive or donor.sustenance <= 0:
                continue
            # An offspring may be drawn on only once it is mature AND standing with
            # the parent — newborns are protected, and the payoff is deferred and
            # conditional (true insurance, not a birth-time subsidy). Peers and the
            # reverse direction (a parent aiding its own young child) are unaffected.
            if ag.id in (donor.parent_ids or []):
                mature = (self._tick_no - donor.born_tick) >= w.maturity_age_ticks
                colocated = donor.pos_x == ag.pos_x and donor.pos_y == ag.pos_y
                if not (mature and colocated):
                    continue
            give = min(donor.sustenance, need - pulled)
            if give <= 0:
                continue
            donor.sustenance -= give
            pulled += give
            await asyncio.to_thread(
                self.store.update_agent_state, donor.id, sustenance=donor.sustenance
            )
            # mutual aid deepens the tie both ways
            await asyncio.to_thread(
                self.store.upsert_relationship, ag.id, donor.id, r.get("type") or "",
                w.mutual_aid_bonus, "kept me going", self._tick_no,
            )
            await asyncio.to_thread(
                self.store.upsert_relationship, donor.id, ag.id, "",
                w.mutual_aid_bonus, "I kept them going", self._tick_no,
            )
        if pulled <= 0:
            return
        ag.vitality = min(ceiling, ag.vitality + pulled * w.vitality_per_sustenance)
        self._bump("mutual_aid_transfers")
        await asyncio.to_thread(
            self.store.record_event, "mutual_aid",
            {"to_id": ag.id, "amount": round(pulled, 1), "tick": self._tick_no},
        )
        await self.memory.add_summary(
            ag.id, f"I was failing, and those I am bound to kept me going.",
            tick_no=self._tick_no,
        )

    # ----- ecology: the spatial substrate (the grid beings move on and work) -----

    def _patch_label(self, x: int, y: int) -> str:
        col = chr(ord("A") + x) if 0 <= x < 26 else str(x)
        return f"{col}{y + 1}"

    def _parse_patch_label(self, s: str) -> tuple[int, int] | None:
        s = (s or "").strip().upper().strip("[]()<> ")
        if len(s) < 2 or not s[0].isalpha() or not s[1:].isdigit():
            return None
        x = ord(s[0]) - ord("A")
        y = int(s[1:]) - 1
        eco = self.cfg.world.ecology
        if 0 <= x < eco.grid_width and 0 <= y < eco.grid_height:
            return (x, y)
        return None

    async def _init_ecology(self) -> None:
        eco = self.cfg.world.ecology
        rows = []
        for y in range(eco.grid_height):
            for x in range(eco.grid_width):
                res = eco.patch_capacity * eco.initial_patch_fill_frac
                self._patches[(x, y)] = {
                    "x": x, "y": y, "resource": res,
                    "capacity": eco.patch_capacity, "regen": eco.patch_regen_per_tick,
                }
                rows.append({"x": x, "y": y, "resource": res,
                             "capacity": eco.patch_capacity,
                             "regen": eco.patch_regen_per_tick, "tick": 0})
        await asyncio.to_thread(self.store.init_patches, rows)
        # scatter founders across the grid so neighbours and good ground must be
        # sought — locality and the co-harvest bonus then pull beings together.
        coords = [(x, y) for y in range(eco.grid_height) for x in range(eco.grid_width)]
        self._rng.shuffle(coords)
        for i, ag in enumerate(self.agents.values()):
            ag.pos_x, ag.pos_y = coords[i % len(coords)]

    async def _load_ecology(self) -> None:
        rows = await asyncio.to_thread(self.store.list_patches)
        for r in rows:
            self._patches[(int(r["x"]), int(r["y"]))] = {
                "x": int(r["x"]), "y": int(r["y"]),
                "resource": float(r["resource"]), "capacity": float(r["capacity"]),
                "regen": float(r["regen"]),
            }

    async def _regen_patches(self) -> None:
        updates = []
        for (x, y), p in self._patches.items():
            if p["resource"] < p["capacity"]:
                p["resource"] = min(p["capacity"], p["resource"] + p["regen"])
                updates.append((p["resource"], self._tick_no, x, y))
        if updates:
            await asyncio.to_thread(self.store.bulk_update_patch_resources, updates)

    async def _do_move(self, ag: Agent, act: dict) -> None:
        eco = self.cfg.world.ecology
        tx, ty = ag.pos_x, ag.pos_y
        parsed = self._parse_patch_label(str(act.get("to"))) if act.get("to") else None
        if parsed:
            tx, ty = parsed
        else:
            try:
                tx = ag.pos_x + int(act.get("dx") or 0)
                ty = ag.pos_y + int(act.get("dy") or 0)
            except (TypeError, ValueError):
                pass
        tx = max(0, min(eco.grid_width - 1, tx))
        ty = max(0, min(eco.grid_height - 1, ty))
        # you can only walk as far as you can see — one stride toward the target.
        if max(abs(tx - ag.pos_x), abs(ty - ag.pos_y)) > eco.perception_radius:
            sx = (tx > ag.pos_x) - (tx < ag.pos_x)
            sy = (ty > ag.pos_y) - (ty < ag.pos_y)
            tx, ty = ag.pos_x + sx, ag.pos_y + sy
        if (tx, ty) == (ag.pos_x, ag.pos_y):
            return
        if eco.move_vitality_cost > 0 and ag.alive:
            ag.vitality -= eco.move_vitality_cost
        ag.pos_x, ag.pos_y = tx, ty
        await asyncio.to_thread(
            self.store.update_agent_state, ag.id,
            vitality=ag.vitality, pos_x=tx, pos_y=ty,
        )
        await asyncio.to_thread(
            self.store.record_event, "move",
            {"agent_id": ag.id, "to": self._patch_label(tx, ty), "tick": self._tick_no},
        )
        await self.memory.add_summary(
            ag.id, f"I moved to {self._patch_label(tx, ty)}.", tick_no=self._tick_no,
        )

    async def _resolve_harvests(self) -> None:
        """All beings who chose to work this tick draw on the patch they stand on,
        together. The per-head take rises with the number of co-present harvesters
        (capped), so working shoulder to shoulder beats working alone — the whole
        cooperation incentive, by arithmetic, with no rule demanding it."""
        if not self._harvest_intents:
            return
        eco = self.cfg.world.ecology
        w = self.cfg.world
        by_patch: dict[tuple[int, int], list[Agent]] = {}
        for ag, x, y in self._harvest_intents:
            if ag.alive:
                by_patch.setdefault((x, y), []).append(ag)
        self._harvest_intents = []
        updates = []
        for (x, y), crew in by_patch.items():
            patch = self._patches.get((x, y))
            if not patch:
                continue
            n = len(crew)
            per_head = eco.harvest_base_yield + eco.coharvest_bonus_per_partner * min(
                eco.coharvest_max_partners, n - 1
            )
            # each being's pull also scales with its heritable labor efficiency
            # (and is reduced while a successor is still maturing).
            def _mature(a: Agent) -> bool:
                return not (
                    a.parent_ids
                    and (self._tick_no - a.born_tick) < w.maturity_age_ticks
                )
            def _mult(a: Agent) -> float:
                m = float(a.traits.get("labor_efficiency", 1.0))
                if not _mature(a):
                    m *= w.child_effort_factor
                return m
            # A grown being working shoulder to shoulder with a grown parent or
            # child of its own draws a little extra — kin labor, paid through the
            # same harvest channel as any co-presence. It needs both to be grown,
            # so a raised successor only repays the lineage once it has matured.
            def _has_grown_kin(a: Agent) -> bool:
                if not _mature(a):
                    return False
                return any(
                    o is not a and _mature(o)
                    and (o.id in (a.parent_ids or []) or a.id in (o.parent_ids or []))
                    for o in crew
                )
            demands = [
                per_head * _mult(a)
                + (w.kin_coharvest_bonus if _has_grown_kin(a) else 0.0)
                for a in crew
            ]
            total = sum(demands) or 1.0
            drawn = min(total, patch["resource"])
            scale = drawn / total
            patch["resource"] = max(0.0, patch["resource"] - drawn)
            updates.append((patch["resource"], self._tick_no, x, y))
            shares: dict[str, float] = {}
            for a, dem in zip(crew, demands):
                got = dem * scale
                shares[a.id] = round(got, 2)
                a.sustenance += got
                if w.sustenance_max > 0 and a.sustenance > w.sustenance_max:
                    a.sustenance = w.sustenance_max
                await asyncio.to_thread(
                    self.store.update_agent_state, a.id, sustenance=a.sustenance
                )
            self._bump("cooperative_work" if n > 1 else "solo_work")
            await asyncio.to_thread(
                self.store.record_event, "harvest",
                {"patch": self._patch_label(x, y), "harvesters": [a.id for a in crew],
                 "drawn": round(drawn, 1), "shares": shares, "tick": self._tick_no},
            )
            note = (f"We worked {self._patch_label(x, y)} together — {n} of us"
                    if n > 1 else f"I worked {self._patch_label(x, y)} alone")
            for a in crew:
                await self.memory.add_summary(a.id, note + ".", tick_no=self._tick_no)
        if updates:
            await asyncio.to_thread(self.store.bulk_update_patch_resources, updates)

    def _local_view(self, ag: Agent) -> dict:
        """What `ag` can sense: its patch, the patches within sight, and who is
        co-present or nearby. Drives concrete perception and localised speech."""
        eco = self.cfg.world.ecology
        r = eco.perception_radius
        here = self._patches.get((ag.pos_x, ag.pos_y))
        around = []
        for (x, y), p in self._patches.items():
            if (x, y) == (ag.pos_x, ag.pos_y):
                continue
            if max(abs(x - ag.pos_x), abs(y - ag.pos_y)) <= r:
                around.append({
                    "label": self._patch_label(x, y),
                    "resource": p["resource"], "capacity": p["capacity"],
                })
        around.sort(key=lambda d: d["resource"], reverse=True)
        here_ids, near_ids = [], []
        for other in self.agents.values():
            if not other.alive or other.id == ag.id:
                continue
            d = max(abs(other.pos_x - ag.pos_x), abs(other.pos_y - ag.pos_y))
            if d == 0:
                here_ids.append(other.id)
            elif d <= r:
                near_ids.append(other.id)
        return {
            "here_label": self._patch_label(ag.pos_x, ag.pos_y),
            "here": {"resource": here["resource"], "capacity": here["capacity"]} if here else None,
            "around": around,
            "here_ids": here_ids,
            "near_ids": near_ids,
        }

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
            # A being perceives only its locale: nearby patches and the beings
            # here/near it. That both grounds its speech in concrete particulars
            # and limits whom it can readily address.
            local_view = self._local_view(a)
            near = set(local_view["here_ids"]) | set(local_view["near_ids"])
            agent_peers = [a] + [
                self.agents[i] for i in near if i in self.agents
            ]
            msgs, stub_ctx = self.prompts.decide_action_messages(
                a, agent_peers, medium, long_term, recent_events, forced, inbox_lines,
                world_state=world_state, relationships=rels, local_view=local_view,
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

        # The free `express` channel: a being may voice a signal into the shared
        # world without spending its one action on it. Without this, in the
        # spatial world the action is always spent on move/harvest and the
        # commons (cultural_artifacts) is never fed.
        for ag, dec in decisions:
            if not ag.alive:
                continue
            act = dec.get("action")
            if isinstance(act, dict) and \
                    str(act.get("verb") or "").strip().lower() == "express":
                continue  # already voiced via the action verb; don't duplicate
            voiced = str(dec.get("express") or "").strip()
            if not voiced:
                continue
            try:
                await self._do_express(
                    ag, {"content": voiced, "remix": dec.get("express_remix")}
                )
            except Exception as exc:
                self.console.log(f"[red]express error for {ag.id}[/]: {exc}")

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
        elif verb == "move":
            await self._do_move(ag, act)
        elif verb == "express":
            await self._do_express(ag, act)
        elif verb in ("tie", "bond"):
            await self._do_bond(ag, act)
        elif verb == "dissolve":
            await self._do_dissolve(ag, act)
        elif verb == "share":
            await self._do_share(ag, act)
        elif verb == "bear_successor":
            await self._do_bear_successor(ag, act)
        # unknown verbs are silently ignored (lenient by design)

    async def _do_work(self, ag: Agent, act: dict) -> None:
        w = self.cfg.world
        self._bump("work_actions")
        # Labor tires: working costs vitality, so it is a genuine trade-off
        # against the sustenance it yields.
        if w.work_vitality_cost > 0 and ag.alive:
            ag.vitality -= w.work_vitality_cost
            await asyncio.to_thread(
                self.store.update_agent_state, ag.id, vitality=ag.vitality
            )
        # Work = harvest the patch you stand on. All harvesters are resolved
        # together after the decision pass (see _resolve_harvests), so a
        # co-present crew each draws more than a lone worker would.
        self._harvest_intents.append((ag, ag.pos_x, ag.pos_y))

    async def _do_express(self, ag: Agent, act: dict) -> None:
        content = str(act.get("content") or "").strip()
        if not content:
            return
        # Culture is a free signal; the being need not file it under a human
        # category. `kind` is whatever (if anything) it volunteers.
        kind = str(act.get("kind") or "").strip().lower()[:20]
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
            f"{ag.name} put something out for all to see: {content[:160]}",
        )
        await self.memory.add_summary(
            ag.id, f"I put something out for the others: {content[:120]}",
            tick_no=self._tick_no,
        )
        self.console.log(
            f"[cyan]expression[/]: {ag.name}"
            + (f" ({kind})" if kind else "") + f" #{aid}"
        )

    async def _do_bond(self, ag: Agent, act: dict) -> None:
        tgt = self._resolve_agent_ref(act.get("target"))
        if not tgt or tgt.id == ag.id:
            return
        # A tie is a directed, weighted connection. The being names it (or not)
        # in its own words — there is no fixed menu of human relationship kinds.
        label = str(act.get("label") or act.get("type") or "").strip().lower()[:30]
        note = str(act.get("note") or "").strip()[:160]
        await asyncio.to_thread(
            self.store.upsert_relationship,
            ag.id, tgt.id, label, 1.0, note, self._tick_no,
        )
        self._bump("bonds_formed")
        await asyncio.to_thread(
            self.store.record_event, "bond",
            {"from_id": ag.id, "to_id": tgt.id, "label": label, "note": note},
        )
        await self.memory.add_summary(
            ag.id, f"I keep turning toward {tgt.name}"
            + (f" — {label}" if label else "")
            + (f" ({note})" if note else "") + ".", tick_no=self._tick_no,
        )
        self.console.log(
            f"[magenta]tie[/]: {ag.name} -> {tgt.name}"
            + (f" ({label})" if label else "")
        )

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
        # A gift builds the giver's tie in proportion to its size (no fixed
        # human label). If the receiver was already bound to the giver, the gift
        # answers an existing bond — reciprocity — and deepens it both ways.
        w = self.cfg.world
        delta = max(0.5, amount * w.share_strength_per_unit)
        await asyncio.to_thread(
            self.store.upsert_relationship,
            ag.id, tgt.id, "", delta, "gave sustenance", self._tick_no,
        )
        back = await asyncio.to_thread(self.store.get_relationship, tgt.id, ag.id)
        if back and float(back.get("strength") or 0) > 0:
            await asyncio.to_thread(
                self.store.upsert_relationship,
                tgt.id, ag.id, back.get("type") or "", w.mutual_aid_bonus,
                "answered my giving", self._tick_no,
            )
            await asyncio.to_thread(
                self.store.upsert_relationship,
                ag.id, tgt.id, "", w.mutual_aid_bonus, "we look after each other",
                self._tick_no,
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

    async def _do_bear_successor(self, ag: Agent, act: dict) -> None:
        w = self.cfg.world
        living = sum(1 for a in self.agents.values() if a.alive)
        if living >= w.max_population:
            return
        partner = self._resolve_agent_ref(act.get("partner"))
        if not partner or partner.id == ag.id or not partner.alive:
            return
        # A new being needs a strong enough bond between the two — but NOT a
        # bond of any particular named kind. What the tie is called is the
        # beings' own affair; only its strength gates continuity.
        rel = await asyncio.to_thread(
            self.store.get_relationship, ag.id, partner.id
        )
        strength = float((rel or {}).get("strength") or 0)
        if not rel or strength < w.succession_min_partner_strength:
            return
        # Fecundity (heritable) lowers the real cost of bringing a being forth.
        cost = w.succession_sustenance_cost / max(0.1, float(ag.traits.get("fecundity", 1.0)))
        if ag.sustenance < cost:
            return
        ag.sustenance -= cost
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
        # Birth forges a naturally strong reciprocal tie between each parent and
        # the child — no named kind, just strength. The offspring thus counts as a
        # bond the parent can later be kept going by, and vice versa: bringing a
        # being forth becomes insurance, not only altruism toward the future.
        for parent in (ag, partner):
            await asyncio.to_thread(
                self.store.upsert_relationship, parent.id, child.id, "",
                w.kin_bond_strength, "brought into being", self._tick_no,
            )
            await asyncio.to_thread(
                self.store.upsert_relationship, child.id, parent.id, "",
                w.kin_bond_strength, "who brought me into being", self._tick_no,
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
        from ..agents import Agent as _Agent, default_self_model, default_traits
        w = self.cfg.world
        self._birth_seq += 1
        cid = f"born_{self._birth_seq}_{self._tick_no}"
        # Guard against ever colliding with an existing (incl. resumed) agent.
        while cid in self.agents:
            self._birth_seq += 1
            cid = f"born_{self._birth_seq}_{self._tick_no}"
        # No imposed kinship label: the child's name, if unnamed, is just its id.
        child_name = str(name).strip()[:40] if name else cid
        # Heritable biology: a mutated blend of both parents. Lineages that hit
        # on a winning combination out-survive and out-breed → the trait
        # distribution drifts under selection, with no rule prescribing it.
        t1, t2 = p1.traits or {}, p2.traits or {}
        child_traits: dict[str, float] = {}
        for k, dflt in default_traits().items():
            avg = (float(t1.get(k, dflt)) + float(t2.get(k, dflt))) / 2.0
            mutated = avg * (1.0 + self._rng.gauss(0.0, w.trait_mutation_sigma))
            child_traits[k] = max(w.trait_min, min(w.trait_max, mutated))
        sm1, sm2 = p1.self_model or {}, p2.self_model or {}
        # blend: inherit a narrative seeded by both, merge values, take a few
        # carried signals from each, and an inherited goal.
        values = {}
        values.update(sm2.get("values") or {})
        values.update(sm1.get("values") or {})
        carried = list(dict.fromkeys(
            (sm1.get("carried") or [])[:2] + (sm2.get("carried") or [])[:2]
        ))
        narrative = (
            f"I came into being from {p1.name} and {p2.name}. I carry something "
            "of them; what I make of it is for me to find out."
        )
        child_sm = default_self_model(
            narrative,
            values=dict(list(values.items())[:5]),
            carried=carried,
            goals=(sm1.get("goals") or [])[:1],
            relationships_summary=f"Came into being from {p1.name} and {p2.name}.",
        )
        child = _Agent(
            id=cid,
            name=child_name,
            gender="",
            circumstance="",
            disposition="",
            self_model=child_sm,
            self_model_version=0,
            vitality=w.vitality_initial,
            sustenance=w.child_sustenance_initial,
            born_tick=self._tick_no,
            parent_ids=[p1.id, p2.id],
            traits=child_traits,
            pos_x=p1.pos_x,
            pos_y=p1.pos_y,
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
        """An agent going along with a signal from the commons: carry it and
        record the adoption (cultural transmission + lineage)."""
        if not isinstance(adopt_ids, list):
            return
        carried = new_model.get("carried")
        if not isinstance(carried, list):  # defensive: tolerate a mangled field
            carried = [str(carried)] if carried not in (None, "", [], {}) else []
            new_model["carried"] = carried
        for raw_id in adopt_ids[:5]:
            try:
                art_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            art = commons_by_id.get(art_id)
            if not art or art.get("author_id") == ag.id:
                continue  # can't "adopt" your own, and unknown ids ignored
            signal = (art.get("content") or "").strip()[:200]
            if signal and signal not in carried:
                carried.append(signal)
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
        for k in ("identity_narrative", "relationships_summary"):
            if (old.get(k) or "") != (new.get(k) or ""):
                changes.append(f"{k} changed")
        for k in ("carried", "goals"):
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
                "cooperative_work": self.metrics["cooperative_work"],
                "solo_work": self.metrics["solo_work"],
                "mutual_aid_transfers": self.metrics["mutual_aid_transfers"],
                "artifacts_authored": self.metrics["artifacts_authored"],
                "adoptions": self.metrics["adoptions"],
                "bonds_formed": self.metrics["bonds_formed"],
                "shares": self.metrics["shares"],
                "births": self.metrics["births"],
                "max_generation": max(
                    (self._generation_depth(a) for a in self.agents.values()
                     if a.alive), default=0),
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
