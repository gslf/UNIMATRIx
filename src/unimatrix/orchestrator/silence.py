"""Anti-silence trigger.

If silence_detection_seconds elapse with no active conversation and no open
vote, the orchestrator picks the N agents with the lowest social_need and
forces them to interact on their next decision (the prompt removes the
"do_nothing" and "join_group" options).
"""
from __future__ import annotations

from ..agents import Agent


def pick_forced_agents(agents: list[Agent], n: int) -> list[Agent]:
    if n <= 0:
        return []
    eligible = [a for a in agents if a.state.value == "idle"]
    eligible.sort(key=lambda a: a.social_need)
    return eligible[:n]
