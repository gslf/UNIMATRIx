"""Canonical serialization used by requests, events and immutable manifests."""

import hashlib
import json


def canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest_text(text: str) -> str:
    """Hash text that is already canonical JSON, avoiding a second serialization."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(value) -> str:
    return digest_text(canonical(value))


def entity_id(run, tick, slot, index):
    return digest([run, tick, slot, index])[:24]
