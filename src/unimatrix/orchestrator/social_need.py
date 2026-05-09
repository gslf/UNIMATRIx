"""Social-need decay maths.

Linear decay applied once per tick; recharge handled inside the conversation
engine on each turn.
"""
from __future__ import annotations

from typing import Iterable

from ..agents import Agent


def decay(agents: Iterable[Agent], decay_per_tick: float) -> None:
    if decay_per_tick <= 0:
        return
    for a in agents:
        # Broadcast listeners and voters still decay; active conversation
        # participants recharge from turns instead of losing need while engaged.
        if a.is_busy():
            continue
        a.social_need = max(0.0, a.social_need - decay_per_tick)
