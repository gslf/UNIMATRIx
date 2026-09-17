"""Descriptive material, social and compute outcomes, separate from USI."""

import json
from statistics import fmean


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
    events = list(store.events())
    resources = sorted({r for a in state.agents.values() for r in a["inventory"]})
    stocks = {r: [a["inventory"].get(r, 0) for a in state.agents.values()] for r in resources}
    calls = [json.loads(row[0]) for row in store.db.execute("SELECT body FROM model_calls")]
    messages = [e for e in events if e["type"] == "message_sent"]
    edges = {
        (e["actor_id"], recipient)
        for e in messages
        for recipient in e["payload"]["to"]
        if e["actor_id"] != recipient
    }
    n = len(state.agents)
    model_slots = {
        slot for slot, policy in store.manifest["policies"].items() if isinstance(policy, dict)
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
        births=sum(e["type"] == "agent_born" for e in events),
        deaths=sum(e["type"] == "agent_died" for e in events),
        broken_contracts=sum(e["type"] == "contract_breached" for e in events),
    )
    return dict(
        inventory_by_resource={r: dict(total=sum(v), gini=gini(v)) for r, v in stocks.items()},
        social=social,
        living_agents=sum(a["alive"] for a in state.agents.values()),
        messages=len(messages),
        rejected_operations=sum(e["type"] == "operation_rejected" for e in events),
        invalid_envelopes=sum(e["type"] == "decision_rejected" for e in events),
        decision_attempts=len(calls),
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
