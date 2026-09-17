"""Random access to exogenous randomness; actions never advance a cursor."""

from .ids import digest


class RandomTape:
    version = "sha256-v1"

    def __init__(self, seed):
        self.seed = seed

    def integer(self, tick, entity, kind, upper):
        if upper <= 0:
            raise ValueError("positive upper bound required")
        return int(digest([self.version, self.seed, tick, entity, kind]), 16) % upper

    def priority(self, tick, slots):
        return sorted(slots, key=lambda slot: (self.integer(tick, slot, "priority", 2**256), slot))
