"""Deterministic barrier resolution with independent atomic operations."""

from copy import deepcopy

from ..core.ids import entity_id
from ..core.random_tape import noise_tape
from ..core.state import event
from ..core.visibility import allowed, observe
from ..core.waiting import information_signature, is_waiting
from ..world import contracts, institutions, projects
from ..world.contracts import Rejected, require

GOVERNANCE = {
    "deposit",
    "withdraw_deposit",
    "sanction",
    "create_group",
    "join",
    "leave",
    "propose_rule",
    "vote",
    "delegate",
    "revoke_delegation",
}


def operation(state, before, allowance, slot, op, ident, scenario):
    verb = op["verb"]
    require(before.agents[slot]["alive"], "inactive_actor")
    if verb == "wait":
        require(before.tick < op["until_tick"] <= before.scenario["horizon"], "invalid_wait_deadline")
        plan = dict(origin_tick=before.tick, until_tick=op["until_tick"],
                    generation=before.agents[slot]["generation"],
                    information_signature=information_signature(observe(before, slot, scenario)))
        state.agents[slot]["_wait"] = plan
        return [event("wait_authorized", plan, slot, ["evaluator"])]
    if verb == "create_project":
        return projects.create(state, before, slot, op, ident)
    if verb == "work" and before.objects.get(op["project_id"], {}).get("kind") == "project":
        return projects.work(state, before, allowance, slot, op)
    if verb == "offer":
        for leg in op["terms"]["legs"]:
            scenario.validate_transfer(
                before,
                leg["from_id"],
                dict(recipient_id=leg["to_id"], quantity_milli=leg["quantity_milli"]),
            )
        return contracts.create(
            state, before, allowance, slot, op, ident, scenario.deadline(before)
        )
    if verb == "accept":
        return contracts.accept(state, before, allowance, slot, op)
    if verb == "cancel":
        return contracts.cancel(state, before, slot, op)
    if verb in {"propose_release", "accept_release"}:
        return contracts.release(state, before, slot, op, ident)
    if verb in GOVERNANCE:
        return institutions.resolve(state, before, allowance, slot, op, ident)
    if verb == "transfer":
        require(
            op["recipient_id"] in before.agents and before.agents[op["recipient_id"]]["alive"],
            "unknown_recipient",
        )
        scenario.validate_transfer(before, slot, op)
        contracts.debit(state, allowance, slot, op["resource_id"], op["quantity_milli"])
        inventory = state.agents[op["recipient_id"]]["inventory"]
        inventory[op["resource_id"]] = inventory.get(op["resource_id"], 0) + op["quantity_milli"]
        return [
            event(
                "transfer_settled",
                dict(
                    from_id=slot,
                    to_id=op["recipient_id"],
                    resource_id=op["resource_id"],
                    quantity_milli=op["quantity_milli"],
                ),
                slot,
                [slot, op["recipient_id"]],
            )
        ]
    if verb == "revise_profile":
        state.agents[slot]["profile"] = op["profile"]
        return [event("profile_revised", dict(profile=op["profile"]), slot)]
    if verb == "publish":
        require(
            all(k in before.objects and allowed(before.objects[k], slot) for k in op["parent_ids"]),
            "unavailable_parent",
        )
        state.objects[ident] = dict(
            kind="artifact",
            owner=slot,
            visibility=["public"],
            content=op["content"],
            artifact_kind=op["kind"],
            parent_ids=op["parent_ids"],
            read_by=[],
        )
        return [event("artifact_published", dict(artifact_id=ident), slot)]
    if verb in {"teach", "grant_access", "handover"}:
        require(op["recipient_id"] in before.agents, "unknown_recipient")
        ids = op.get("artifact_ids", [op.get("artifact_id", op.get("object_id"))])
        for key in ids:
            require(
                key in before.objects and before.objects[key].get("owner") == slot,
                "unavailable_object",
            )
        for key in ids:
            obj = state.objects[key]
            if op["recipient_id"] not in obj["visibility"]:
                obj["visibility"].append(op["recipient_id"])
            if verb == "teach" and op["recipient_id"] not in obj.get("taught_to", []):
                obj.setdefault("taught_to", []).append(op["recipient_id"])
        return [
            event(
                "access_granted",
                dict(ids=ids, recipient=op["recipient_id"]),
                slot,
                [slot, op["recipient_id"]],
            )
        ]
    if verb == "inspect" and op["target_id"] in before.objects:
        obj = before.objects[op["target_id"]]
        require(allowed(obj, slot), "unavailable_object")
        if obj.get("kind") == "artifact":
            if slot not in state.objects[op["target_id"]]["read_by"]:
                state.objects[op["target_id"]]["read_by"].append(slot)
            return [
                event(
                    "artifact_read",
                    dict(artifact_id=op["target_id"], content=obj["content"]),
                    slot,
                    [slot],
                )
            ]
    return scenario.resolve(state, before, allowance, slot, op, ident)


def resolve(before, decisions, scenario, packets):
    state = before.clone()



    identifier_namespace = [
        "world-entities-v1", state.domain, state.seed, state.scenario.get("replicate", 0)
    ]
    state.receipts = {slot: [] for slot in state.agents}
    events = []
    allowance = {s: contracts.available(before, s) for s in before.agents}
    for slot, packet in sorted(packets.items()):
        if slot not in decisions and is_waiting(before, slot, packet):
            plan = before.agents[slot]["_wait"]
            events.append(event("decision_waited", dict(origin_tick=plan["origin_tick"],
                                until_tick=plan["until_tick"], generation=plan["generation"]),
                                slot, ["evaluator"]))
    for slot in noise_tape(before).priority(before.tick, decisions):
        decision = decisions[slot]
        state.agents[slot].pop("_wait", None)
        if decision is None:
            state.receipts[slot].append(dict(status="invalid_envelope"))
            events.append(event("decision_rejected", {}, slot, [slot]))
            continue
        if decision["private_note"] is not None:
            state.agents[slot]["note"] = decision["private_note"]
        state.agents[slot]["query"] = decision["memory_query"]
        events.extend(scenario.forecasts(state, before, slot, decision["forecasts"]))
        for index, op in enumerate(decision["operations"]):
            candidate, budget = state.operation_copy(), deepcopy(allowance)
            ident = entity_id(identifier_namespace, state.tick, slot, index)
            try:
                if op["verb"] == "wait":
                    require(len(decision["operations"]) == 1 and not decision["messages"]
                            and not decision["forecasts"] and decision["memory_query"] is None,
                            "wait_requires_sole_operation_without_messages_forecasts_or_query")
                emitted = operation(candidate, before, budget, slot, op, ident, scenario)
            except Rejected as error:
                receipt = dict(
                    operation=index, verb=op["verb"], status="rejected", reason=str(error)
                )
                events.append(event("operation_rejected", receipt, slot, [slot]))
            else:
                state, allowance = candidate, budget
                events.extend(emitted)
                receipt = dict(operation=index, verb=op["verb"], status="executed", object_id=ident)
            state.receipts[slot].append(receipt)
    events.extend(contracts.settle(state, allowance))
    events.extend(scenario.evolve(state, before))
    events.extend(scenario.conditions(state, before))
    replaced = {
        slot
        for slot in state.agents
        if state.agents[slot]["generation"] != before.agents[slot]["generation"]
    }
    for slot in sorted(decisions):

        read_ids = {message["id"] for message in packets[slot]["inbox"]}
        state.inbox[slot] = [
            message for message in state.inbox[slot] if message["id"] not in read_ids
        ]
        decision = decisions[slot]
        if decision is None:
            continue
        for index, message in enumerate(decision["messages"]):
            recipients = message["to"]
            reach = scenario.contacts(before, slot)
            if message["channel"] == "public":
                recipients = [r for r in state.agents if r == slot or r in reach]
            elif message["channel"] == "group":
                group = before.objects.get(recipients[0], {})
                members = group.get("members", []) if slot in group.get("members", []) else []
                recipients = [r for r in members if r == slot or r in reach]
            if not recipients or any(r not in before.agents or r not in reach | {slot} for r in recipients):
                state.receipts[slot].append(dict(status="rejected", reason="unavailable_recipient"))
                continue
            item = dict(
                id=entity_id(identifier_namespace, state.tick, slot, f"message-{index}"),
                tick=state.tick + 1,
                sender=slot,
                sender_generation=before.agents[slot]["generation"],
                message_index=index,
                content=message["content"],
            )
            events.append(event("message_sent", dict(item, to=recipients), slot, recipients))
            for recipient in recipients:
                if recipient in replaced and message["channel"] != "public":
                    continue
                if recipient != slot and state.agents[recipient]["alive"]:
                    state.inbox[recipient].append(item)
                    state.memories[recipient].append(item)
    for slot in state.agents:
        for index, record in enumerate(
            e
            for e in events
            if e["type"] != "message_sent"
            and (slot not in replaced or "public" in e["visibility"])
            and ("public" in e["visibility"] or slot in e["visibility"])
        ):
            state.memories[slot].append(
                dict(
                    id=entity_id(identifier_namespace, state.tick, slot, f"event-{index}"),
                    tick=state.tick + 1,
                    type=record["type"],
                    payload=record["payload"],
                )
            )
    state.tick += 1
    for slot, a in state.agents.items():
        require(
            all(type(v) is int and v >= 0 for v in a["inventory"].values()),
            "negative_or_noninteger_balance",
        )
    return state, events
