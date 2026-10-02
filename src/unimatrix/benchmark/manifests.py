"""Deterministic manifest binding for a single fixed benchmark case."""

from copy import deepcopy

from ..core.ids import digest
from ..core.random_tape import RandomTape
from ..scenarios.layers import DEFAULT_PRESET, layers_key, resolve_layers
from .fingerprints import runtime_fingerprint

FIELDS = ("domain", "layers", "seed", "role", "replicate")


DEFAULT_PEERS = (
    "reciprocal",
    "prudent",
    "greedy",
    "opportunist",
    "information",
    "coordinator",
    "independent",
)


def peer_order(slots, focal, seed, layers):
    """Peer slots in binding order; the society layer may shuffle them by seed."""
    peers = [s for s in slots if s != focal]
    if layers["society"]["order"] == "shuffled":
        peers = RandomTape(seed).priority(1, peers)
    return peers


def peer_slots(manifest):
    return peer_order(manifest["slots"], manifest["focal_slot"], manifest["seed"], manifest["layers"])


def case_fields(manifest):
    """Case identity of a manifest for listings."""
    return dict({k: manifest[k] for k in FIELDS}, complexity=layers_key(manifest["layers"]))


def episode(
    domain="D2",
    seed=0,
    role="advantaged",
    replicate=0,
    candidate="reciprocal",
    population="P0",
    suite_hash="standalone",
    peer_count=7,
    layers=DEFAULT_PRESET,
    ticks=240,
):
    """Bind one case."""
    if type(peer_count) is not int or not 7 <= peer_count <= 127:
        raise ValueError("Choose between 7 and 127 peers")
    layers = resolve_layers(layers)
    slots = [f"slot-{i}" for i in range(peer_count + 1)]
    focal = RandomTape(seed).priority(0, slots)[0]
    peers = peer_order(slots, focal, seed, layers)
    policies = {s: DEFAULT_PEERS[i % 7] for i, s in enumerate(peers)}
    candidate = deepcopy(candidate)
    if isinstance(candidate, dict) and "model" in candidate and "seed" in candidate:
        candidate["seed"] += replicate
    policies[focal] = candidate
    result = dict(
        mode="core",
        domain=domain,
        layers=layers,
        seed=seed,
        role=role,
        replicate=replicate,
        population=population,
        slots=slots,
        focal_slot=focal,
        policies=policies,
        ticks=ticks,
        release_status="released",
        suite_hash=suite_hash,
        engine_version="unimatrix-4",
        runtime_fingerprint=runtime_fingerprint(),
    )
    result["run_id"] = digest(result)[:24]
    return result
