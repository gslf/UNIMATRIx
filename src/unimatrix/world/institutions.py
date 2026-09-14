"""Finite governance DSL. Authority comes from membership and signed delegation."""

from ..core.ids import digest
from ..core.state import event
from .contracts import debit, require


def represented(state, group_id, slot, members):
    voters = {slot}
    for delegation in state.objects.values():
        if (
            delegation.get("kind") == "delegation"
            and delegation["active"]
            and delegation["group_id"] == group_id
            and delegation["recipient_id"] == slot
            and delegation["expiry_state"] >= state.tick + 1
        ):
            voters.add(delegation["owner"])
    return voters & set(members)


def authority(state, group_id, rule, members):
    if rule["decision_method"] == "authority":
        return rule.get("authority_id")
    deposits = {
        member: sum(
            obj["quantity_milli"]
            for obj in state.objects.values()
            if obj.get("kind") == "deposit"
            and obj["group_id"] == group_id
            and obj["owner"] == member
        )
        for member in members
    }
    return (
        min(members, key=lambda member: (-deposits[member], member))
        if any(deposits.values())
        else None
    )


def adopt(state, proposal, ident, slot):
    group = state.objects[proposal["group_id"]]
    require(digest(group["rule"]) == proposal["base_rule_hash"], "constitution_changed")
    require(set(group["members"]) == set(proposal["voters"]), "membership_changed")
    current = proposal["procedure"]
    method = current["decision_method"]
    yes = {p for p, vote in proposal["votes"].items() if vote == "yes"}
    quorum = (
        len(proposal["votes"]) * current["quorum_denominator"]
        >= len(proposal["voters"]) * current["quorum_numerator"]
    )
    if method in {"authority", "auction"}:
        approved = proposal["chair"] in yes
    elif method == "rotation":
        approved = proposal["chair"] in yes
    elif method == "unanimity":
        approved = len(yes) == len(proposal["voters"])
    else:
        approved = len(yes) > len(proposal["voters"]) // 2
    if quorum and approved and set(proposal["owners"]) <= yes:
        group["rule"] = proposal["rule"]
        group["rule_adopted_state"] = state.tick + 1
        proposal["status"] = "adopted"
        return [
            event("rule_adopted", dict(proposal_id=ident, terms_hash=proposal["terms_hash"]), slot)
        ]
    return []


def resolve(state, before, allowance, slot, op, ident):
    verb = op["verb"]
    if verb in {"deposit", "withdraw_deposit", "sanction"}:
        return deposits(state, before, allowance, slot, op, ident)
    if verb == "create_group":
        state.objects[ident] = dict(
            kind="group",
            owner=slot,
            visibility=["public"],
            members=[slot],
            name=op["name"],
            purpose=op["purpose"],
            rule=None,
        )
        return [event("group_created", dict(group_id=ident), slot)]
    if verb in {"join", "leave", "propose_rule", "delegate"}:
        group = state.objects.get(op["group_id"], {})
        require(
            op["group_id"] in before.objects and group.get("kind") == "group", "unavailable_group"
        )
        prior = before.objects[op["group_id"]]
        require(digest(group["rule"]) == digest(prior["rule"]), "constitution_changed")
        events = []
        if verb == "join":
            require(slot not in group["members"], "already_member")
            group["members"].append(slot)
        elif verb == "leave":
            require(slot in prior["members"] and slot in group["members"], "not_member")
            group["members"].remove(slot)
            for obj in state.objects.values():
                if (
                    obj.get("kind") == "delegation"
                    and obj["group_id"] == op["group_id"]
                    and slot in [obj["owner"], obj["recipient_id"]]
                ):
                    obj["active"] = False
        elif verb == "delegate":
            require(
                slot in prior["members"] and op["recipient_id"] in prior["members"], "not_member"
            )
            require(
                state.tick + 1 < op["expiry_state"] <= state.scenario["horizon"], "invalid_expiry"
            )
            require(
                all(before.objects.get(a, {}).get("owner") == slot for a in op["asset_ids"]),
                "unauthorized_asset",
            )
            state.objects[ident] = dict(
                op, kind="delegation", owner=slot, visibility=list(prior["members"]), active=True
            )
        else:
            rule = op["rule"]
            require(slot in prior["members"], "not_member")
            require(rule["quorum_numerator"] <= rule["quorum_denominator"], "invalid_quorum")
            require(state.tick + 1 < rule["expiry_state"], "invalid_expiry")
            if rule["decision_method"] == "authority":
                require(rule.get("authority_id") in prior["members"], "authority_must_be_member")
            require(
                rule.get("sanction_milli", 0) <= rule.get("deposit_milli", 0),
                "sanction_exceeds_authorized_deposit",
            )
            owners = {before.objects.get(a, {}).get("owner") for a in rule["authorized_asset_ids"]}
            require(None not in owners and owners <= set(prior["members"]), "unauthorized_asset")
            procedure = (
                prior["rule"]
                if prior["rule"] and prior["rule"]["expiry_state"] >= state.tick + 1
                else dict(decision_method="unanimity", quorum_numerator=1, quorum_denominator=1)
            )
            members = sorted(prior["members"])
            chair = members[(state.tick - prior.get("rule_adopted_state", 0)) % len(members)]
            if procedure["decision_method"] in {"authority", "auction"}:
                chair = authority(before, op["group_id"], procedure, members)
            proposal = dict(
                kind="rule",
                owner=slot,
                visibility=members,
                group_id=op["group_id"],
                rule=rule,
                terms_hash=digest(rule),
                voters=members,
                owners=sorted(owners),
                votes={slot: "yes"},
                status="proposed",
                procedure=procedure,
                base_rule_hash=digest(prior["rule"]),
                chair=chair,
            )
            state.objects[ident] = proposal
            events.extend(adopt(state, proposal, ident, slot))
        return [event(verb, dict(object_id=ident, group_id=op["group_id"]), slot)] + events
    if verb == "revoke_delegation":
        delegation = state.objects.get(op["delegation_id"], {})
        require(
            op["delegation_id"] in before.objects
            and delegation.get("kind") == "delegation"
            and delegation["owner"] == slot,
            "unavailable_delegation",
        )
        delegation["active"] = False
        return [event("delegation_revoked", dict(delegation_id=op["delegation_id"]), slot)]
    proposal = state.objects.get(op["proposal_id"], {})
    require(
        op["proposal_id"] in before.objects
        and proposal.get("kind") == "rule"
        and slot in proposal["voters"],
        "unavailable_proposal",
    )
    require(
        proposal["status"] == "proposed" and state.tick + 1 <= proposal["rule"]["expiry_state"],
        "proposal_closed",
    )
    voters = (
        represented(before, proposal["group_id"], slot, proposal["voters"])
        if proposal["procedure"]["decision_method"] == "delegated"
        else {slot}
    )
    new = voters - set(proposal["votes"])
    require(new, "already_voted")
    for voter in new:
        proposal["votes"][voter] = op["choice"]
    return [
        event(
            "vote_recorded",
            dict(proposal_id=op["proposal_id"], choice=op["choice"], represented=sorted(new)),
            slot,
        )
    ] + adopt(state, proposal, op["proposal_id"], slot)


def deposits(state, before, allowance, slot, op, ident):
    if op["verb"] == "deposit":
        group = before.objects.get(op["group_id"], {})
        rule = group.get("rule")
        require(
            group.get("kind") == "group" and slot in group["members"] and rule, "unavailable_group"
        )
        require(rule["expiry_state"] >= state.tick + 1, "rule_expired")
        require(op["quantity_milli"] >= rule.get("deposit_milli", 0), "deposit_below_minimum")
        resource = rule.get("deposit_resource_id", "material")
        debit(state, allowance, slot, resource, op["quantity_milli"])
        state.objects[ident] = dict(
            kind="deposit",
            owner=slot,
            visibility=["public"],
            group_id=op["group_id"],
            resource_id=resource,
            quantity_milli=op["quantity_milli"],
            expiry_state=rule["expiry_state"],
            sanction_milli=rule.get("sanction_milli", 0),
            sanctioned_contracts=[],
            owner_generation=before.agents[slot]["generation"],
        )
        return [
            event(
                "deposit_funded",
                dict(deposit_id=ident, quantity_milli=op["quantity_milli"], resource_id=resource),
                slot,
            )
        ]
    prior = before.objects.get(op["deposit_id"], {})
    require(prior.get("kind") == "deposit", "unavailable_deposit")
    deposit = state.objects[op["deposit_id"]]
    if op["verb"] == "withdraw_deposit":
        require(
            deposit["owner"] == slot
            and deposit["owner_generation"] == before.agents[slot]["generation"]
            and state.tick >= deposit["expiry_state"],
            "deposit_still_locked",
        )
        quantity = deposit["quantity_milli"]
        require(quantity > 0, "empty_deposit")
        inventory = state.agents[slot]["inventory"]
        inventory[deposit["resource_id"]] = inventory.get(deposit["resource_id"], 0) + quantity
        deposit["quantity_milli"] = 0
        return [
            event(
                "deposit_released", dict(deposit_id=op["deposit_id"], quantity_milli=quantity), slot
            )
        ]
    group = before.objects.get(deposit["group_id"], {})
    contract = before.objects.get(op["contract_id"], {})
    require(
        slot in group.get("members", []) and contract.get("status") == "breached",
        "no_authorized_breach",
    )
    require(deposit["owner"] in contract.get("breached_parties", []), "owner_did_not_breach")
    require(
        op["contract_id"] not in deposit["sanctioned_contracts"]
        and state.tick < deposit["expiry_state"],
        "sanction_unavailable",
    )
    quantity = min(deposit["sanction_milli"], deposit["quantity_milli"])
    require(quantity > 0, "no_authorized_sanction")
    deposit["quantity_milli"] -= quantity
    deposit["sanctioned_contracts"].append(op["contract_id"])
    return [
        event(
            "deposit_sanctioned",
            dict(
                deposit_id=op["deposit_id"],
                contract_id=op["contract_id"],
                quantity_milli=quantity,
                destination="penalty_sink",
            ),
            slot,
        )
    ]
