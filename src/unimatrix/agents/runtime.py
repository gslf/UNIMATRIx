"""In-memory agent runtime: lightweight state for the orchestrator's hot path.

The persistent state lives in SQLite; this object is a fast cache used during
the tick. We snapshot back to SQLite when state changes (role, class,
social_need crossing thresholds, conversation transitions) — not on every
field mutation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..config import AgentSpec, PersonalitySpec


class AgentState(str, Enum):
    IDLE = "idle"
    IN_1TO1 = "in_1to1"
    IN_GROUP = "in_group"
    LISTENING_BROADCAST = "listening_broadcast"
    VOTING = "voting"


@dataclass
class Agent:
    id: str
    name: str
    gender: str
    role: str
    klass: str
    personality: PersonalitySpec
    values: dict[str, int]
    backstory: str
    initial_opinions: dict[str, str]
    social_need: float = 100.0
    state: AgentState = AgentState.IDLE
    current_conversation_id: int | None = None
    last_action_ts: float = 0.0  # monotonic; used by cooldown checks

    @classmethod
    def from_spec(cls, spec: AgentSpec, social_need_initial: float) -> "Agent":
        return cls(
            id=spec.id,
            name=spec.name,
            gender=spec.gender,
            role=spec.role_initial,
            klass=spec.class_initial,
            personality=spec.personality,
            values=dict(spec.values),
            backstory=spec.backstory,
            initial_opinions=dict(spec.initial_opinions),
            social_need=social_need_initial,
        )

    def to_db_row(self) -> dict:
        return {
            "agent_id": self.id,
            "name": self.name,
            "gender": self.gender,
            "role": self.role,
            "class": self.klass,
            "personality": self.personality.model_dump(),
            "values": self.values,
            "backstory": self.backstory,
            "social_need": self.social_need,
            "state": self.state.value,
            "current_conversation_id": self.current_conversation_id,
        }

    def is_busy(self) -> bool:
        return self.state in (AgentState.IN_1TO1, AgentState.IN_GROUP)
