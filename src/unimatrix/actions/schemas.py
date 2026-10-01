"""Strict envelope validation; material failures are per-operation receipts."""

import json
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from ..core.ids import canonical
from .availability import WORLD_OPERATIONS, implemented_verbs

SCHEMA = json.loads(Path(__file__).with_name("decision.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


OPERATIONS = {
    variant["properties"]["verb"]["const"]: Draft202012Validator(variant)
    for variant in SCHEMA["properties"]["operations"]["items"]["oneOf"]
}
_ENVELOPE = deepcopy(SCHEMA)
_ENVELOPE["properties"]["operations"]["items"] = dict(
    type="object", required=["verb"], properties=dict(verb=dict(enum=sorted(OPERATIONS)))
)
ENVELOPE = Draft202012Validator(_ENVELOPE)


def empty(tick, slot):
    return dict(
        protocol="unimatrix.decision.v4",
        tick=tick,
        agent_id=slot,
        messages=[],
        operations=[],
        private_note=None,
        memory_query=None,
        forecasts=[],
    )


def validate(raw, tick, slot):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 6144:
        raise ValueError("envelope_byte_budget")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    data = json.loads(
        raw,
        object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
    )
    ENVELOPE.validate(data)
    for op in data["operations"]:
        OPERATIONS[op["verb"]].validate(op)
    if data["tick"] != tick or data["agent_id"] != slot:
        raise ValueError("identity_or_tick_mismatch")
    for text, limit in [(data["private_note"], 4000), (data["memory_query"], 500)] + [
        (m["content"], 1200) for m in data["messages"]
    ]:
        if text is not None and len(text.encode("utf-8")) > limit:
            raise ValueError("utf8_byte_budget")

    def strict_numbers(value, key=""):
        integer_fields = {
            "tick",
            "until_tick",
            "quantity_milli",
            "cost_milli",
            "deposit_milli",
            "sanction_milli",
            "expiry_state",
            "settlement_state",
            "quorum_numerator",
            "quorum_denominator",
            "spend_limit_milli",
            "output_slot",
        }
        if key in integer_fields and type(value) is not int:
            raise ValueError("integer_required")
        if isinstance(value, dict):
            for name, child in value.items():
                strict_numbers(child, name)
        elif isinstance(value, list):
            for child in value:
                if key == "input_slots" and type(child) is not int:
                    raise ValueError("integer_required")
                strict_numbers(child)

    strict_numbers(data)
    canonical(data)
    return data


def interface(domain=None):
    """Implemented operation types, identical for every actor in the same domain."""

    def shape(spec):
        if "const" in spec:
            return spec["const"]
        if "enum" in spec:
            return "|".join(str(v) for v in spec["enum"])
        if spec.get("type") == "object":
            return {k: shape(v) for k, v in spec["properties"].items()}
        if spec.get("type") == "array":
            return [shape(spec["items"])]
        return spec.get("type", "value")

    operations = {
        op["properties"]["verb"]["const"]: {
            k: shape(v) for k, v in op["properties"].items()
        }
        for op in SCHEMA["properties"]["operations"]["items"]["oneOf"]
        if op["properties"]["verb"]["const"] in implemented_verbs(domain, OPERATIONS)
    }
    return dict(
        envelope=empty(0, "YOUR_SLOT"),
        operations=operations,
        message={
            "private": dict(channel="private", to=["agent-id"], content="text"),
            "public": dict(channel="public", to=[], content="text"),
            "group": dict(channel="group", to=["group-id"], content="text"),
        },
        forecast=dict(probe_id="issued-probe-id", probabilities=[0.5, 0.5]),
        limits=dict(
            operations=2,
            messages=2,
            private_message_recipients=4,
            public_message_recipients=0,
            group_message_recipients=1,
            message_recipients_unique=True,
            message_utf8_bytes=1200,
            private_note_utf8_bytes=4000,
            envelope_utf8_bytes=6144,
        ),
        notes="Each operations item is an object with a verb field. Use the current tick and your authenticated slot. Null private_note preserves notes; empty string clears them. Sign the exact terms_hash. Quantities are positive integer milliunits; message delivery is next tick. wait(until_tick) suspends your decisions until that tick or any observable information change/new message. Use wait as the sole operation, with no messages, forecasts or memory_query; private_note may be saved. The world and deadlines continue.",
    )



INTERFACE = interface()
INTERFACES = {domain: interface(domain) for domain in WORLD_OPERATIONS}
