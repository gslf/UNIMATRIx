"""In-memory agent runtime: lightweight state for the orchestrator's hot path.

The persistent state lives in SQLite; this object is a fast cache used during
the tick. We snapshot back to SQLite when state changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..config import AgentSpec


class AgentState(str, Enum):
    IDLE = "idle"
    DEAD = "dead"


def default_self_model(
    identity_narrative: str = "",
    *,
    values: dict[str, str] | None = None,
    beliefs: list[str] | None = None,
    goals: list[str] | None = None,
    relationships_summary: str = "",
    mortality_stance: str = "",
) -> dict:
    return {
        "identity_narrative": identity_narrative,
        "values": values or {},
        "beliefs": beliefs or [],
        "goals": goals or [],
        "relationships_summary": relationships_summary,
        "mortality_stance": mortality_stance,
    }


@dataclass
class Agent:
    id: str
    name: str
    gender: str
    # --- thin seed (provisional, fades as the self-model grows) ---
    circumstance: str = ""
    disposition: str = ""
    # --- the evolving self ---
    self_model: dict = field(default_factory=default_self_model)
    self_model_version: int = 0
    # --- finitude & sustenance ---
    vitality: float = 100.0
    alive: bool = True
    sustenance: float = 0.0
    # --- continuity / lineage ---
    born_tick: int = 0
    parent_ids: list[str] = field(default_factory=list)
    # --- drives / bookkeeping ---
    social_need: float = 100.0
    state: AgentState = AgentState.IDLE

    @classmethod
    def from_spec(
        cls,
        spec: AgentSpec,
        social_need_initial: float,
        vitality_initial: float = 100.0,
        sustenance_initial: float = 0.0,
        blank_slate: bool = False,
    ) -> "Agent":
        circumstance = "" if blank_slate else (spec.circumstance or "")
        disposition = "" if blank_slate else (spec.disposition or "")
        # The opening identity is just the provisional disposition; the agent
        # rewrites it from experience on its first self-revision.
        opening = "" if blank_slate else disposition
        return cls(
            id=spec.id,
            name=spec.name,
            gender=spec.gender,
            circumstance=circumstance,
            disposition=disposition,
            self_model=default_self_model(opening),
            vitality=vitality_initial,
            sustenance=sustenance_initial,
            social_need=social_need_initial,
        )

    def to_db_row(self) -> dict:
        return {
            "agent_id": self.id,
            "name": self.name,
            "gender": self.gender,
            "circumstance": self.circumstance,
            "disposition": self.disposition,
            "self_model_json": self.self_model,
            "self_model_version": self.self_model_version,
            "vitality": self.vitality,
            "alive": self.alive,
            "sustenance": self.sustenance,
            "born_tick": self.born_tick,
            "parent_ids": self.parent_ids,
            "social_need": self.social_need,
            "state": self.state.value,
        }
