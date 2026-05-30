"""Social-mobility engine — automatic role/class movement.

Replaces vote-driven mobility. Each tick (after the economy pass) we recompute,
per agent:

  * ORDINARY ROLE from prestige — the role (excluding the three elected offices)
    whose nominal prestige is the greatest value <= the agent's prestige. A
    "no-op band" keeps the current role as long as prestige stays inside that
    role's [lower, upper) prestige band, so a freshly voter-assigned role (or a
    role that shares a prestige value with another, e.g. scholar/artist=65) does
    not flip spuriously on the next tick. Officeholders are skipped (their role
    mirrors their office).

  * CLASS from popularity AND bank balance — the highest class (by `classes`
    order, index 0 = highest) for which BOTH popularity >= popularity_min and
    balance >= balance_min (inclusive). Drop below either and the agent is
    demoted. Officeholders are pinned to the top class.

prestige/popularity are clamped to [0, 100]. Role/class changes are mirrored to
SQLite and recorded as `status_change` rows with change_type 'role'/'class' so
the graph renderer keeps working unchanged.
"""
from __future__ import annotations

import asyncio

from rich.console import Console

from ..agents import Agent
from ..config import Config
from ..persistence import RunStore


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, float(v)))


class MobilityEngine:
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
        self._role_prestige: dict[str, int] = {r.id: r.prestige for r in cfg.roles}
        offices = set(cfg.office_ids())
        # Ordinary roles by descending nominal prestige (stable: config order
        # breaks prestige ties).
        ordinary = [r for r in cfg.roles if r.id not in offices]
        self._ordinary = sorted(ordinary, key=lambda r: -r.prestige)
        self._ordinary_ids = {r.id for r in self._ordinary}
        self._class_order = list(cfg.classes)  # index 0 = highest
        self._thresholds = cfg.resolved_class_thresholds()

    # ----- pure mapping helpers (reused by election + init reconciliation) -----

    @property
    def top_class(self) -> str:
        return self._class_order[0]

    def role_prestige(self, role_id: str) -> int:
        return self._role_prestige.get(role_id, 0)

    def ordinary_role_for_prestige(self, prestige: float) -> str:
        """Highest ordinary role whose nominal prestige <= `prestige`; the
        lowest ordinary role if `prestige` is below all of them."""
        for r in self._ordinary:  # descending prestige
            if r.prestige <= prestige:
                return r.id
        return self._ordinary[-1].id

    def _ordinary_band(self, role_id: str) -> tuple[float, float] | None:
        """[lower, upper) prestige band of an ordinary role, or None if the id
        is not an ordinary role."""
        if role_id not in self._ordinary_ids:
            return None
        p = self._role_prestige[role_id]
        highers = [r.prestige for r in self._ordinary if r.prestige > p]
        upper = float(min(highers)) if highers else float("inf")
        return (float(p), upper)

    def class_for(self, popularity: float, balance: float) -> str:
        for c in self._class_order:  # highest first
            t = self._thresholds[c]
            if popularity >= t.popularity_min and balance >= t.balance_min:
                return c
        return self._class_order[-1]

    def initial_popularity_for_class(self, class_id: str) -> float:
        t = self._thresholds.get(class_id)
        return float(t.popularity_min) if t else 0.0

    # ----- per-tick passes -----

    async def decay(self) -> None:
        """Optional per-tick erosion of prestige/popularity (default 0)."""
        pd = self.cfg.mobility.prestige_decay_per_tick
        qd = self.cfg.mobility.popularity_decay_per_tick
        if pd <= 0 and qd <= 0:
            return
        for ag in self.agents.values():
            before = (ag.prestige, ag.popularity)
            if pd > 0:
                ag.prestige = _clamp(ag.prestige - pd)
            if qd > 0:
                ag.popularity = _clamp(ag.popularity - qd)
            if (ag.prestige, ag.popularity) != before:
                await asyncio.to_thread(
                    self.store.update_agent_state,
                    ag.id,
                    prestige=ag.prestige,
                    popularity=ag.popularity,
                )

    async def recompute(self) -> None:
        """Recompute every agent's ordinary role (from prestige) and class
        (from popularity + balance). Officeholders keep their office role and
        are pinned to the top class."""
        for ag in self.agents.values():
            # Defensive clamp.
            ag.prestige = _clamp(ag.prestige)
            ag.popularity = _clamp(ag.popularity)

            # --- ordinary role from prestige (skip officeholders) ---
            if ag.office is None:
                band = self._ordinary_band(ag.role)
                if band is not None and band[0] <= ag.prestige < band[1]:
                    new_role = ag.role  # still prestige-consistent → no flip
                else:
                    new_role = self.ordinary_role_for_prestige(ag.prestige)
                if new_role != ag.role:
                    old = ag.role
                    ag.role = new_role
                    await asyncio.to_thread(
                        self.store.update_agent_state, ag.id, role=ag.role
                    )
                    await asyncio.to_thread(
                        self.store.record_status_change,
                        ag.id, "role", old, ag.role, None,
                    )
                    await asyncio.to_thread(
                        self.store.record_event,
                        "mobility_role_change",
                        {"agent_id": ag.id, "from": old, "to": ag.role,
                         "prestige": ag.prestige},
                    )
                    self.console.log(
                        f"[dim]mobility[/] {ag.id}: role {old} → {ag.role} "
                        f"(prestige {ag.prestige:.0f})"
                    )

            # --- class from popularity + balance (officeholders pinned) ---
            if ag.office is not None:
                target_class = self.top_class
            else:
                target_class = self.class_for(ag.popularity, ag.bank_account)
            if target_class != ag.klass:
                old_c = ag.klass
                ag.klass = target_class
                await asyncio.to_thread(
                    self.store.update_agent_state, ag.id, klass=ag.klass
                )
                await asyncio.to_thread(
                    self.store.record_status_change,
                    ag.id, "class", old_c, ag.klass, None,
                )
                await asyncio.to_thread(
                    self.store.record_event,
                    "mobility_class_change",
                    {"agent_id": ag.id, "from": old_c, "to": ag.klass,
                     "popularity": ag.popularity,
                     "balance": round(ag.bank_account, 2)},
                )
                self.console.log(
                    f"[dim]mobility[/] {ag.id}: class {old_c} → {ag.klass} "
                    f"(pop {ag.popularity:.0f}, bal {ag.bank_account:.0f})"
                )
