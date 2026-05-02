"""Social-need decay maths.

Linear decay over wall-clock time; recharge handled inside the conversation
engine on each turn. The decay rate is configured per minute; we apply it in
proportion to the elapsed seconds since the last call so the value is correct
regardless of tick jitter.
"""
from __future__ import annotations

from typing import Iterable

from ..agents import Agent


def decay(agents: Iterable[Agent], elapsed_seconds: float, decay_per_minute: float) -> None:
    if elapsed_seconds <= 0 or decay_per_minute <= 0:
        return
    delta = decay_per_minute * (elapsed_seconds / 60.0)
    for a in agents:
        # Broadcast listeners and voters still decay; active conversation
        # participants recharge from turns instead of losing need while engaged.
        if a.is_busy():
            continue
        a.social_need = max(0.0, a.social_need - delta)
