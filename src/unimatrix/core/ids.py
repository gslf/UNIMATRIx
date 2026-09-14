"""Canonical serialization used by requests, events and immutable manifests."""

import hashlib
import json


def canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def entity_id(run, tick, slot, index):
    return digest([run, tick, slot, index])[:24]
