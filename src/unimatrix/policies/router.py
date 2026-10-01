"""Explicit slot bindings; no access to WorldState."""

from .scripted import Scripted


class Router:
    def __init__(self, bindings):
        self.bindings = bindings
        self.state = None

    def begin_tick(self, state):
        """Privileged reference policies read the world the runner is about to observe."""
        self.state = state

    def policy(self, slot):
        return self.bindings[slot]

    @classmethod
    def scripted(cls, manifest):
        from ..core.random_tape import noise_seed

        seed = noise_seed(manifest["seed"], manifest["replicate"])
        return cls(
            {
                slot: Scripted(name, seed, slot == manifest["focal_slot"])
                for slot, name in manifest["policies"].items()
            }
        )
