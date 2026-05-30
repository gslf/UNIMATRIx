"""In-memory agent runtime: lightweight state for the orchestrator's hot path.

The persistent state lives in SQLite; this object is a fast cache used during
the tick. We snapshot back to SQLite when state changes (role, class,
social_need crossing thresholds, conversation transitions) — not on every
field mutation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..config import AgentSpec, PersonalitySpec


class AgentState(str, Enum):
    IDLE = "idle"
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
    opinions: dict[str, str]
    social_need: float = 100.0
    state: AgentState = AgentState.IDLE
    bank_account: float = 0.0
    destitute: bool = False
    prestige: float = 0.0
    popularity: float = 0.0
    office: str | None = None
    power_blocked: bool = False

    @classmethod
    def from_spec(
        cls,
        spec: AgentSpec,
        social_need_initial: float,
        initial_balance: float = 0.0,
        prestige_initial: float | None = None,
        popularity_initial: float | None = None,
        office: str | None = None,
    ) -> "Agent":
        return cls(
            id=spec.id,
            name=spec.name,
            gender=spec.gender,
            role=spec.role_initial,
            klass=spec.class_initial,
            personality=spec.personality,
            values=dict(spec.values),
            backstory=spec.backstory,
            opinions=dict(spec.opinions),
            social_need=social_need_initial,
            bank_account=initial_balance,
            prestige=prestige_initial if prestige_initial is not None else 0.0,
            popularity=popularity_initial if popularity_initial is not None else 0.0,
            office=office,
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
            "bank_account": self.bank_account,
            "destitute": self.destitute,
            "prestige": self.prestige,
            "popularity": self.popularity,
            "office": self.office,
        }

    def is_busy(self) -> bool:
        return False
