"""Social-need decay maths.

Linear decay applied once per tick to every agent; recharge happens in the
messaging engine when an agent sends or reads messages.
"""
from __future__ import annotations

from typing import Iterable

from ..agents import Agent


def decay(agents: Iterable[Agent], decay_per_tick: float) -> None:
    if decay_per_tick <= 0:
        return
    for a in agents:
        a.social_need = max(0.0, a.social_need - decay_per_tick)
