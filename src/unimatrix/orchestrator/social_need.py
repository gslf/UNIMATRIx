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
        # Only decay when the agent isn't actively engaged; decay also applies
        # to broadcast-listeners and voters since they're not having a real
        # conversation, but the gain inside engaged conversations dominates.
        a.social_need = max(0.0, a.social_need - delta)
