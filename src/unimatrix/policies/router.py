"""Explicit slot bindings; no access to WorldState."""

from .scripted import Scripted


class Router:
    def __init__(self, bindings):
        self.bindings = bindings

    def policy(self, slot):
        return self.bindings[slot]

    @classmethod
    def scripted(cls, manifest):
        return cls({slot: Scripted(name) for slot, name in manifest["policies"].items()})
