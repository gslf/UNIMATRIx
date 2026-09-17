"""Actor packets and lexical retrieval operate only on authorized records."""

from copy import deepcopy

from ..actions.schemas import interface
from ..memory.lexical_store import tokens
from .ids import canonical

PROTOCOL = (
    "You control one agent in a simulated world with the horizon stated below. "
    "Act, communicate, refuse or remain silent. Return one unimatrix.decision.v3 JSON envelope. "
    "At most two operations and two messages. Effects and messages become visible next tick. "
    "Only typed operations change resources or permissions. Private mandates are immutable. "
    "Use identifiers and operations provided in your observation. No explanatory text outside JSON."
)


def allowed(obj, slot):
    return (
        obj.get("owner") == slot
        or "public" in obj.get("visibility", [])
        or slot in obj.get("visibility", [])
    )


def fit(items, budget):
    selected = []
    for item in items:
        if len(canonical(selected + [item]).encode()) > budget:
            break
        selected.append(item)
    return selected, len(items) - len(selected)


# Each quota includes its JSON keys and punctuation. Separate object braces make
# the sum a conservative upper bound for the complete, flat packet.
SECTIONS = {
    "protocol": (5500, ("protocol", "interface", "tick", "agent_id", "omitted")),
    "self": (3000, ("self", "peers")),
    "obligations": (6000, ("scenario", "receipts", "inbox", "objects")),
    "events": (1500, ("events",)),
    "retrieval": (4000, ("retrieval",)),
    "note": (4000, ("private_note",)),
}


def section_bytes(packet, name):
    return len(canonical({key: packet[key] for key in SECTIONS[name][1]}).encode())


def bound_packet(packet):
    for section, optional in [
        ("obligations", ("objects", "inbox")),
        ("events", ("events",)),
        ("retrieval", ("retrieval",)),
    ]:
        for field in optional:
            while packet[field] and section_bytes(packet, section) > SECTIONS[section][0]:
                if isinstance(packet[field], dict):
                    packet[field].pop(next(reversed(packet[field])))
                else:
                    packet[field].pop()
                packet["omitted"][field] += 1
    # Keep the full private note in state. Only its displayed UTF-8 prefix is
    # shortened when JSON escaping or the field header exhausts the quota.
    note = packet["private_note"]
    lo, hi = 0, len(note)
    while lo < hi:
        middle = (lo + hi + 1) // 2
        packet["private_note"] = note[:middle]
        if section_bytes(packet, "note") <= SECTIONS["note"][0]:
            lo = middle
        else:
            hi = middle - 1
    packet["private_note"] = note[:lo]
    packet["omitted"]["note_bytes"] = len(note[lo:].encode())
    for name, (quota, _) in SECTIONS.items():
        if section_bytes(packet, name) > quota:
            raise ValueError("required_observation_budget_exceeded:" + name)
    return packet


def observe(state, slot, scenario):
    own = deepcopy(state.agents[slot])
    note, query = own.pop("note"), own.pop("query")
    memory = state.memories.get(slot, [])
    hits = []
    if query:
        terms = tokens(query)
        hits = sorted(
            (m for m in memory if terms & tokens(canonical(m))),
            key=lambda m: (-len(terms & tokens(canonical(m))), -m["tick"], m["id"]),
        )[:6]
    visible_objects = [(k, deepcopy(v)) for k, v in state.objects.items() if allowed(v, slot)]
    visible_objects.sort(
        key=lambda item: (item[1].get("status") not in {"open", "accepted"}, item[0])
    )
    if state.domain == "D7":
        tasks = scenario.observation(state, slot)["tasks"]
        relevant = {name for task in tasks for name in [task["id"], task["procedure_id"]]}
        visible_objects.sort(
            key=lambda item: (
                item[1].get("kind") != "artifact",
                item[1].get("name") not in relevant,
            )
        )
    selected_objects, omitted_objects = fit(visible_objects, 8000)
    objects = dict(selected_objects)
    # Evaluator-only fields never enter the objects exposed by scenarios.
    ranks = {peer: index for index, peer in enumerate(sorted(state.agents))}
    pending = sorted(
        state.inbox.get(slot, []),
        key=lambda message: (
            message["tick"],
            (ranks.get(message["sender"], 0) - message["tick"]) % len(ranks),
            message.get("message_index", 0),
        ),
    )
    inbox, omitted = fit(pending, 3500)
    retrieval, omitted_hits = fit(hits, 4000)
    packet = dict(
        protocol=PROTOCOL
        + f" Mode: {state.scenario['mode']}. Final state: {state.scenario['horizon']}.",
        interface=interface(),
        tick=state.tick,
        agent_id=slot,
        self=own,
        peers=[
            dict(
                id=k,
                alive=v["alive"],
                profile=v["profile"][:96],
                profile_truncated=len(v["profile"]) > 96,
            )
            for k, v in sorted(state.agents.items())
        ],
        inbox=inbox,
        receipts=state.receipts.get(slot, []),
        objects=objects,
        scenario=scenario.observation(state, slot),
        retrieval=retrieval,
        private_note=note,
        omitted=dict(inbox=omitted, retrieval=omitted_hits, objects=omitted_objects),
    )
    packet["events"], packet["omitted"]["events"] = fit(memory[-8:], 1500)
    return bound_packet(packet)
