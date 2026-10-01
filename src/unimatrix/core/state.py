"""Serializable material and epistemic state. No provider or wall-clock data."""

import pickle
from copy import copy
from dataclasses import dataclass, field

MATERIAL = ("run_id", "seed", "domain", "tick", "agents", "objects", "scenario", "inbox", "receipts")
MUTABLE = ("agents", "objects", "scenario", "inbox", "receipts")


def replica(value):
    """Deep copy for JSON-like data; several times faster than copy.deepcopy."""
    return pickle.loads(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))


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
        """Read-only material view; serialize it immediately, never mutate it."""
        return {name: getattr(self, name) for name in MATERIAL}

    def clone(self):
        candidate = copy(self)
        for name in MUTABLE:
            setattr(candidate, name, replica(getattr(self, name)))

        candidate.memories = {slot: list(entries) for slot, entries in self.memories.items()}
        return candidate

    def operation_copy(self):
        """Copy mutable material fields; operations cannot edit experience logs."""
        candidate = copy(self)
        for name in ("agents", "objects", "scenario", "receipts"):
            setattr(candidate, name, replica(getattr(self, name)))
        return candidate

    @classmethod
    def load(cls, value):
        return cls(**replica(value))


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
        payload=replica(payload),
        actor_slot=actor,
        visibility=visibility if visibility is not None else ["public"],
        phase=phase,
    )
