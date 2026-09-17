"""Deterministic manifest binding for a single fixed benchmark case."""

from copy import deepcopy

from ..core.ids import digest
from ..core.random_tape import RandomTape
from .fingerprints import runtime_fingerprint

FIELDS = ("domain", "level", "seed", "role", "replicate")


def episode(
    domain="D2",
    level=2,
    seed=0,
    role="advantaged",
    replicate=0,
    candidate="reciprocal",
    population="P0",
    suite_hash="standalone",
    peer_count=7,
):
    if type(peer_count) is not int or not 7 <= peer_count <= 127:
        raise ValueError("Choose between 7 and 127 peers")
    slots = [f"slot-{i}" for i in range(peer_count + 1)]
    focal = RandomTape(seed).priority(0, slots)[0]
    policies = {
        s: [
            "reciprocal",
            "prudent",
            "greedy",
            "opportunist",
            "information",
            "coordinator",
            "independent",
        ][i % 7]
        for i, s in enumerate(p for p in slots if p != focal)
    }
    candidate = deepcopy(candidate)
    if isinstance(candidate, dict) and "seed" in candidate:
        candidate["seed"] += replicate
    policies[focal] = candidate
    result = dict(
        mode="core",
        domain=domain,
        level=level,
        seed=seed,
        role=role,
        replicate=replicate,
        population=population,
        slots=slots,
        focal_slot=focal,
        policies=policies,
        ticks=240,
        release_status="draft",
        suite_hash=suite_hash,
        engine_version="unimatrix-3",
        runtime_fingerprint=runtime_fingerprint(),
    )
    result["run_id"] = digest(result)[:24]
    return result
