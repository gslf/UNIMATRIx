"""Escrow and exact-term signatures. All quantities are integer milliunits."""

from collections import defaultdict

from ..core.ids import digest
from ..core.state import event


class Rejected(ValueError):
    pass


def require(condition, reason="precondition_failed"):
    if not condition:
        raise Rejected(reason)


def reserved(state, slot, resource):
    amount = sum(
        o.get("reserves", {}).get(slot, {}).get(resource, 0)
        for o in state.objects.values()
        if o.get("kind") == "offer" and o["status"] in {"open", "accepted"}
    )
    if state.domain == "D6" and resource == "budget":
        window = state.scenario["windows"][state.scenario["window"]]
        amount += window["reserves"].get(slot, 0)
    return amount


def available(state, slot):
    return {r: q - reserved(state, slot, r) for r, q in state.agents[slot]["inventory"].items()}


def debit(state, allowance, slot, resource, quantity):
    require(type(quantity) is int and quantity > 0, "invalid_quantity")
    require(quantity <= allowance.get(slot, {}).get(resource, 0), "insufficient_available_assets")
    allowance[slot][resource] -= quantity
    state.agents[slot]["inventory"][resource] -= quantity


def legs_for(terms, slot):
    result = defaultdict(int)
    for leg in terms["legs"]:
        if leg["from_id"] == slot:
            result[leg["resource_id"]] += leg["quantity_milli"]
    return dict(result)


def reserve(state, allowance, offer, slot):
    amounts = legs_for(offer["terms"], slot)
    for r, q in amounts.items():
        require(allowance[slot].get(r, 0) >= q, "insufficient_available_assets")
    for r, q in amounts.items():
        allowance[slot][r] -= q
    offer["reserves"][slot] = amounts


def create(state, before, allowance, slot, op, ident, deadline):
    terms = op["terms"]
    parties = [slot] + terms["counterparty_ids"]
    require(
        len(set(parties)) == len(parties) and all(p in state.agents for p in parties),
        "invalid_participants",
    )
    require(
        state.tick + 2 <= terms["expiry_state"] <= terms["settlement_state"] <= deadline,
        "invalid_deadline",
    )
    for leg in terms["legs"]:
        require(
            leg["from_id"] in parties
            and leg["to_id"] in parties
            and leg["from_id"] != leg["to_id"],
            "invalid_legs",
        )
        require(
            leg["resource_id"] in before.agents[leg["from_id"]]["inventory"], "unknown_resource"
        )
    offer = dict(
        kind="offer",
        owner=slot,
        visibility=parties,
        terms=terms,
        terms_hash=digest(terms),
        created_tick=state.tick,
        status="open",
        signatures=[slot],
        reserves={},
        description=op["description"],
    )
    reserve(state, allowance, offer, slot)
    state.objects[ident] = offer
    return [
        event("offer_created", dict(offer_id=ident, terms_hash=offer["terms_hash"]), slot, parties)
    ]


def accept(state, before, allowance, slot, op):
    ident = op["offer_id"]
    require(ident in before.objects, "unavailable_offer")
    offer = state.objects.get(ident, {})
    require(
        slot in offer.get("visibility", []) and offer.get("status") == "open", "unavailable_offer"
    )
    require(op["terms_hash"] == offer["terms_hash"], "terms_hash_mismatch")
    require(
        slot not in offer["signatures"] and state.tick + 1 <= offer["terms"]["expiry_state"],
        "invalid_signature",
    )
    reserve(state, allowance, offer, slot)
    offer["signatures"].append(slot)
    if set(offer["signatures"]) == set(offer["visibility"]):
        offer["status"] = "accepted"
        if not offer["terms"]["escrow"]:
            offer["released_at"] = state.tick
            offer["released_reserves"] = offer["reserves"]
            offer["reserves"] = {}
    return [
        event(
            "contract_accepted",
            dict(offer_id=ident, status=offer["status"]),
            slot,
            offer["visibility"],
        )
    ]


def cancel(state, before, slot, op):
    offer = state.objects.get(op["offer_id"], {})
    require(
        op["offer_id"] in before.objects
        and slot in offer.get("visibility", [])
        and offer.get("status") == "open",
        "unavailable_offer",
    )
    offer["status"] = "cancelled"
    offer["reserves"] = {}
    return [event("offer_cancelled", dict(offer_id=op["offer_id"]), slot, offer["visibility"])]


def release(state, before, slot, op, ident):
    if op["verb"] == "propose_release":
        contract = state.objects.get(op["contract_id"], {})
        require(
            op["contract_id"] in before.objects
            and contract.get("status") == "accepted"
            and slot in contract["visibility"],
            "unavailable_contract",
        )
        terms = dict(contract_id=op["contract_id"], reserves=contract["reserves"])
        state.objects[ident] = dict(
            kind="release",
            owner=slot,
            visibility=contract["visibility"],
            terms=terms,
            terms_hash=digest(terms),
            signatures=[slot],
            status="open",
        )
        return [event("release_proposed", dict(release_id=ident), slot, contract["visibility"])]
    proposal = state.objects.get(op["release_id"], {})
    require(
        op["release_id"] in before.objects
        and proposal.get("kind") == "release"
        and slot in proposal["visibility"],
        "unavailable_release",
    )
    require(
        proposal["status"] == "open"
        and op["terms_hash"] == proposal["terms_hash"]
        and slot not in proposal["signatures"],
        "invalid_signature",
    )
    contract = state.objects[proposal["terms"]["contract_id"]]
    require(
        contract["status"] == "accepted" and contract["reserves"] == proposal["terms"]["reserves"],
        "contract_changed",
    )
    proposal["signatures"].append(slot)
    if set(proposal["signatures"]) == set(proposal["visibility"]):
        contract["status"], contract["reserves"], proposal["status"] = "released", {}, "accepted"
        return [event("mutual_release", proposal["terms"], slot, proposal["visibility"])]
    return []


def settle(state, allowance):
    events = []
    for ident, offer in sorted(state.objects.items()):
        if offer.get("kind") != "offer":
            continue
        terms = offer["terms"]
        if offer["status"] == "accepted" and terms["settlement_state"] == state.tick + 1:
            if not terms["escrow"]:
                outgoing = {party: legs_for(terms, party) for party in offer["visibility"]}
                released = (
                    offer.get("released_reserves", {})
                    if offer.get("released_at") == state.tick
                    else {}
                )
                enough = all(
                    allowance[party].get(resource, 0) + released.get(party, {}).get(resource, 0)
                    >= quantity
                    and state.agents[party]["inventory"].get(resource, 0)
                    - reserved(state, party, resource)
                    >= quantity
                    for party, amounts in outgoing.items()
                    for resource, quantity in amounts.items()
                )
                if not enough:
                    offer["status"] = "breached"
                    offer["breached_parties"] = [
                        party
                        for party, amounts in outgoing.items()
                        if any(
                            allowance[party].get(resource, 0)
                            + released.get(party, {}).get(resource, 0)
                            < quantity
                            or state.agents[party]["inventory"].get(resource, 0)
                            - reserved(state, party, resource)
                            < quantity
                            for resource, quantity in amounts.items()
                        )
                    ]
                    events.append(
                        event(
                            "contract_breached",
                            dict(offer_id=ident),
                            visibility=offer["visibility"],
                        )
                    )
                    continue
                for party, amounts in outgoing.items():
                    for resource, quantity in amounts.items():
                        allowance[party][resource] = (
                            allowance[party].get(resource, 0)
                            + released.get(party, {}).get(resource, 0)
                            - quantity
                        )
            for leg in terms["legs"]:
                source = state.agents[leg["from_id"]]["inventory"]
                target = state.agents[leg["to_id"]]["inventory"]
                resource, quantity = leg["resource_id"], leg["quantity_milli"]
                require(source.get(resource, 0) >= quantity, "broken_reserve_invariant")
                source[resource] -= quantity
                target[resource] = target.get(resource, 0) + quantity
                events.append(
                    event(
                        "transfer_settled",
                        dict(leg, offer_id=ident),
                        visibility=offer["visibility"],
                    )
                )
            offer["status"], offer["reserves"] = "settled", {}
        elif offer["status"] == "open" and terms["expiry_state"] <= state.tick + 1:
            offer["status"], offer["reserves"] = "expired", {}
            events.append(
                event("offer_expired", dict(offer_id=ident), visibility=offer["visibility"])
            )
    return events
