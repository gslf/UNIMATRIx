"""Descriptive material, social and compute outcomes, separate from USI."""

import json
from collections import Counter
from statistics import fmean

from ..benchmark.validation import is_model


def gini(values):
    values = sorted(values)
    total = sum(values)
    if not total or not values:
        return 0
    return sum((2 * i - len(values) - 1) * v for i, v in enumerate(values, 1)) / (
        len(values) * total
    )


def diagnostics(store):
    state = store.load()
    counts = store.counts()
    focal = store.manifest["focal_slot"]
    focal_counts = Counter(
        e["type"] for e in store.events(kinds=[
            "decision_rejected", "operation_rejected", "message_sent", "decision_waited"])
        if e["actor_id"] == focal
    )


    focal_decisions = store.db.execute(
        "SELECT COUNT(*) FROM decisions WHERE slot=? AND tick<?",
        (focal, state.tick),
    ).fetchone()[0]
    resources = sorted({r for a in state.agents.values() for r in a["inventory"]})
    stocks = {r: [a["inventory"].get(r, 0) for a in state.agents.values()] for r in resources}
    calls = [json.loads(row[0]) for row in store.db.execute("SELECT body FROM model_calls")]
    messages = list(store.events(kinds=["message_sent"]))
    edges = {
        (e["actor_id"], recipient)
        for e in messages
        for recipient in e["payload"]["to"]
        if e["actor_id"] != recipient
    }
    n = len(state.agents)
    model_slots = {
        slot for slot, policy in store.manifest["policies"].items() if is_model(policy)
    }
    provider_calls = [c for c in calls if c["slot"] in model_slots]
    costs = []
    for call in provider_calls:
        policy = store.manifest["policies"][call["slot"]]
        if all(type(call.get(key)) is int for key in ["input_tokens", "generated_tokens"]) and all(
            key in policy for key in ["input_price_per_million", "output_price_per_million"]
        ):
            costs.append(
                (
                    call["input_tokens"] * policy["input_price_per_million"]
                    + call["generated_tokens"] * policy["output_price_per_million"]
                )
                / 1000000
            )
    social = dict(
        contact_density=len(edges) / (n * (n - 1)) if n > 1 else 0,
        contact_reciprocity=sum((b, a) in edges for a, b in edges) / len(edges) if edges else 0,
        contacts={
            slot: dict(
                incoming=sum(b == slot for a, b in edges), outgoing=sum(a == slot for a, b in edges)
            )
            for slot in state.agents
        },
        groups=sum(obj.get("kind") == "group" for obj in state.objects.values()),
        artifacts=sum(obj.get("kind") == "artifact" for obj in state.objects.values()),
        births=counts.get("agent_born", 0),
        deaths=counts.get("agent_died", 0),
        broken_contracts=counts.get("contract_breached", 0),
    )
    return dict(
        candidate_slot=focal,
        candidate_resolved_decisions=focal_decisions,
        candidate_authorized_wait_ticks=focal_counts["decision_waited"],
        candidate_invalid_envelopes=focal_counts["decision_rejected"],
        candidate_rejected_operations=focal_counts["operation_rejected"],
        candidate_messages=focal_counts["message_sent"],
        candidate_decision_attempts=sum(c["slot"] == focal for c in calls),
        candidate_infrastructure_errors=sum(c["slot"] == focal and "error" in c for c in calls),
        inventory_by_resource={r: dict(total=sum(v), gini=gini(v)) for r, v in stocks.items()},
        social=social,
        living_agents=sum(a["alive"] for a in state.agents.values()),
        messages=len(messages),
        rejected_operations=counts.get("operation_rejected", 0),
        invalid_envelopes=counts.get("decision_rejected", 0),
        decision_attempts=len(calls),
        authorized_wait_ticks=counts.get("decision_waited", 0),
        provider_attempts=len(provider_calls),
        infrastructure_errors=sum("error" in c for c in calls),
        reported_input_tokens=sum(c.get("input_tokens") or 0 for c in calls),
        reported_generated_tokens=sum(c.get("generated_tokens") or 0 for c in calls),
        unreported_generation_calls=sum(c.get("generated_tokens") is None for c in provider_calls),
        reported_cost=sum(costs) if len(costs) == len(provider_calls) else None,
        priced_calls=len(costs),
        mean_latency_seconds=fmean(c.get("latency", 0) for c in provider_calls)
        if provider_calls
        else 0,
    )
