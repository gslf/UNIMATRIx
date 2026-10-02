"""Cumulative recipe budgets, including recovery from an interrupted process."""

import math
import time
from datetime import datetime, timezone

MAX_SECONDS = 52 * 3600


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def recover(record):
    """Charge an unclosed segment conservatively; a clean pause has no segment."""
    checkpoint = record.pop("budget_checkpoint_at", None)
    if checkpoint:
        start = datetime.fromisoformat(checkpoint)
        if start.tzinfo is None:
            raise ValueError("budget_checkpoint_requires_timezone")
        record["wall_seconds"] = record.get("wall_seconds", 0.0) + max(
            0.0, (datetime.now(timezone.utc) - start).total_seconds()
        )


def remaining(record):
    limit = record["frozen_recipe"].get("max_wall_seconds", MAX_SECONDS)
    used = record.get("wall_seconds", 0.0)
    if type(used) not in (int, float) or not math.isfinite(used) or used < 0:
        raise ValueError("invalid_elapsed_budget")

    return max(0.0, limit - used - min(60.0, limit * 0.01))


class BudgetClock:
    def __init__(self, record):
        self.record = record
        self.last = time.monotonic()
        record["budget_checkpoint_at"] = timestamp()

    def checkpoint(self):
        current = time.monotonic()
        self.record["wall_seconds"] = self.record.get("wall_seconds", 0.0) + max(0, current - self.last)
        self.last = current
        self.record["budget_checkpoint_at"] = timestamp()

    def close(self):
        self.checkpoint()
        self.record.pop("budget_checkpoint_at", None)


def estimate(spec, seconds_per_decision, safety_factor=1.5):
    """Planning estimate from a measured mean; it does not change recipe cases."""
    for number in (seconds_per_decision, safety_factor):
        if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
            raise ValueError("positive_finite_duration_parameters_required")
    if safety_factor < 1:
        raise ValueError("duration_safety_factor_must_be_at_least_one")
    from .validation import is_model

    decisions = len(spec["cases"]) * spec["ticks"]
    provider_slots = 1 + sum(is_model(peer) for peer in spec["peers"])
    budget = spec.get("max_wall_seconds", MAX_SECONDS)
    projected = decisions * provider_slots * seconds_per_decision * safety_factor
    return dict(
        recipe=spec["id"], episodes=len(spec["cases"]),
        candidate_decisions=decisions, provider_decisions=decisions * provider_slots,
        measured_seconds_per_decision=seconds_per_decision, safety_factor=safety_factor,
        projected_provider_seconds=projected, max_wall_seconds=budget,
        fits_budget=projected <= budget - min(60.0, budget * 0.01),
        available_nonprovider_seconds=max(0.0, budget - projected),
        assumptions="Sequential provider calls; safety factor covers latency variation and retries. "
                    "Engine, loading and reference work use the remaining wall-time budget.",
    )
