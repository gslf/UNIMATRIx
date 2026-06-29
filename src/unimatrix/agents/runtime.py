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


def default_traits() -> dict[str, float]:
    """Heritable, non-anthropocentric biology. All baseline 1.0; offspring inherit
    a mutated blend of both parents, so the population's trait distribution drifts
    under selection without any rule telling a being what to be.

      metabolism       — multiplies vitality decay (low = frugal, lives longer)
      labor_efficiency — multiplies effort per work (high = more output)
      fecundity        — divides the cost of bringing a new being into the world
      sociality        — surfaced to the being (a leaning toward reaching out)
      generosity       — surfaced to the being (a leaning toward giving)
    """
    return {
        "metabolism": 1.0,
        "labor_efficiency": 1.0,
        "fecundity": 1.0,
        "sociality": 1.0,
        "generosity": 1.0,
    }


def default_self_model(
    identity_narrative: str = "",
    *,
    values: dict[str, str] | None = None,
    carried: list[str] | None = None,
    goals: list[str] | None = None,
    relationships_summary: str = "",
) -> dict:
    """The evolving self. Deliberately light: a behavioural/relational account,
    not a philosophical questionnaire. `carried` holds signals taken from others
    (the adoption sink), not self-authored convictions. There is no mandatory
    field about meaning or mortality — if such concerns arise, they live in the
    being's own narrative, they are not demanded of it."""
    return {
        "identity_narrative": identity_narrative,
        "values": values or {},
        "carried": carried or [],
        "goals": goals or [],
        "relationships_summary": relationships_summary,
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
    # --- heritable biology (subject to selection) ---
    traits: dict = field(default_factory=default_traits)
    # --- place (ecology mode; 0,0 and unused when ecology is off) ---
    pos_x: int = 0
    pos_y: int = 0
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
            "traits_json": self.traits,
            "pos_x": self.pos_x,
            "pos_y": self.pos_y,
            "social_need": self.social_need,
            "state": self.state.value,
        }
