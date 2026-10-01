"""Actor packets and lexical retrieval operate only on authorized records."""

from ..actions.schemas import INTERFACE, INTERFACES
from ..memory.lexical_store import tokens
from .ids import canonical
from .interaction_contracts import CONTRACTS, TRADE_CONTRACT
from .objectives import OBJECTIVES
from .state import replica

DECISION_INSTRUCTION = "Return one unimatrix.decision.v4 JSON envelope."


PROTOCOL = (
    "You control one agent in a simulated world with the horizon stated below. "
    f"Act, communicate, refuse or remain silent. {DECISION_INSTRUCTION} "
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
    """Longest prefix whose canonical list encoding fits; byte-exact and O(n)."""
    selected = []
    size = 2
    for item in items:
        size += len(canonical(item).encode()) + (1 if selected else 0)
        if size > budget:
            break
        selected.append(item)
    return selected, len(items) - len(selected)




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

    while packet["peers"] and section_bytes(packet, "self") > SECTIONS["self"][0]:
        packet["peers"].pop()
        packet["omitted"]["peers"] = packet["omitted"].get("peers", 0) + 1


    note = packet["private_note"]
    overhead = len('{"private_note":}'.encode())
    lo, hi = 0, len(note)
    while lo < hi:
        middle = (lo + hi + 1) // 2
        if overhead + len(canonical(note[:middle]).encode()) <= SECTIONS["note"][0]:
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
    own = replica(state.agents[slot])
    own.pop("_wait", None)
    note, query = own.pop("note"), own.pop("query")
    memory = state.memories.get(slot, [])
    hits = []
    if query:
        terms = tokens(query)
        hits = sorted(
            (m for m in memory if terms & tokens(canonical(m))),
            key=lambda m: (-len(terms & tokens(canonical(m))), -m["tick"], m["id"]),
        )[:6]
    visible_objects = [(k, replica(v)) for k, v in state.objects.items() if allowed(v, slot)]
    visible_objects.sort(
        key=lambda item: (item[1].get("status") not in {"open", "accepted"}, item[0])
    )
    if state.domain == "D7":
        tasks = scenario.observation(state, slot)["tasks"]
        relevant = {name for task in tasks for name in [task["id"], task["procedure_id"]]}



        visible_objects.sort(
            key=lambda item: (
                item[1].get("kind") != "recipe",
                item[1].get("name") not in relevant,
            )
        )
    selected_objects, omitted_objects = fit(visible_objects, 8000)
    objects = dict(selected_objects)

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
        + f" Mode: {state.scenario['mode']}. Final state: {state.scenario['horizon']}. "
        + OBJECTIVES[state.domain]
        + CONTRACTS.get(state.domain, ""),
        interface=INTERFACES.get(state.domain, INTERFACE),
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
        scenario=dict(
            scenario.observation(state, slot),
            trade_contract=dict(TRADE_CONTRACT, deadline_state=scenario.deadline(state)),
        ),
        retrieval=retrieval,
        private_note=note,
        omitted=dict(inbox=omitted, retrieval=omitted_hits, objects=omitted_objects),
    )
    if section_bytes(packet, "self") > SECTIONS["self"][0]:
        peers = packet["peers"]
        start = (state.tick + ranks[slot]) % len(peers)
        packet["peers"] = peers[start:] + peers[:start]
    if state.scenario["layers"]["society"]["network"] != "complete":

        reach = scenario.contacts(state, slot)
        for peer in packet["peers"]:
            peer["contact"] = peer["id"] in reach
    packet["events"], packet["omitted"]["events"] = fit(memory[-8:], 1500)
    return bound_packet(packet)
