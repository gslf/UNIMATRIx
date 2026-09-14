"""Serializable material and epistemic state. No provider or wall-clock data."""

from copy import copy, deepcopy
from dataclasses import asdict, dataclass, field


@dataclass
class WorldState:
    run_id: str
    seed: int
    domain: str
    tick: int = 0
    agents: dict = field(default_factory=dict)
    objects: dict = field(default_factory=dict)
    scenario: dict = field(default_factory=dict)
    inbox: dict = field(default_factory=dict)
    memories: dict = field(default_factory=dict)
    receipts: dict = field(default_factory=dict)

    def dump(self):
        return asdict(self)

    def clone(self):
        return deepcopy(self)

    def operation_copy(self):
        """Copy mutable material fields; operations cannot edit experience logs."""
        candidate = copy(self)
        for name in ("agents", "objects", "scenario", "receipts"):
            setattr(candidate, name, deepcopy(getattr(self, name)))
        return candidate

    @classmethod
    def load(cls, value):
        return cls(**deepcopy(value))


def agent(slot, inventory=None, mandate=None):
    return dict(
        id=slot,
        alive=True,
        generation=0,
        inventory=inventory or {},
        mandate=mandate or {},
        profile="",
        note="",
        query=None,
        location="hub",
    )


def event(kind, payload, actor=None, visibility=None, phase="resolve"):
    return dict(
        type=kind,
        payload=deepcopy(payload),
        actor_slot=actor,
        visibility=visibility if visibility is not None else ["public"],
        phase=phase,
    )
