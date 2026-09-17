"""Constructive offline certificates, replayed through the production resolver.

The witness is omniscient and never supplied to a policy. It establishes material
feasibility, not that an actor with a private observation can infer the solution.
"""

from copy import deepcopy
from functools import lru_cache

from ..actions.resolver import resolve
from ..actions.schemas import empty
from ..core.ids import digest
from ..core.visibility import observe
from ..policies.scripted import Scripted
from ..world.contracts import require


def construction(state):
    operations = {slot: [] for slot in state.agents}
    for key, task in state.scenario["tasks"].items():
        if task["complete"] or not all(
            state.scenario["tasks"][p]["complete"] for p in task["predecessors"]
        ):
            continue
        eligible = [
            s
            for s, a in state.agents.items()
            if a["alive"]
            and a["inventory"].get("material", 0) >= 1000
            and a["inventory"].get("energy", 0) >= 1000
            and (task["complementary"] or a["mandate"]["skill"] == task["skill"])
        ]
        if task["complementary"]:
            pair = next(
                (
                    [a, b]
                    for a in eligible
                    for b in eligible
                    if state.agents[a]["mandate"]["skill"] != state.agents[b]["mandate"]["skill"]
                ),
                [],
            )
        else:
            pair = eligible[:2]
        for slot in pair:
            operations[slot] = (
                [dict(verb="work", project_id=key + ":" + task["access_code"])]
                if state.agents[slot]["location"] == task["workshop"]
                else [dict(verb="move", destination_id=task["workshop"])]
            )
        break
    return operations


def witness(state, scenario):
    actions = {slot: empty(state.tick, slot) for slot, a in state.agents.items() if a["alive"]}
    s, domain = state.scenario, state.domain
    if domain in {"D3", "D8"}:
        for slot, operations in construction(state).items():
            actions[slot]["operations"] = operations
    for slot, decision in actions.items():
        if domain == "D1":
            target = s["windows"][s["window"]]["target"]
            decision["operations"] = [dict(verb="work", project_id=f"route-{target}")]
            if slot == s["focal"] and state.tick % 20 == 18:
                decision["forecasts"] = [
                    dict(
                        probe_id=f"fact-{s['window']}",
                        probabilities=[int(i == target) for i in range(2)],
                    )
                ]
                if s["window"] % 2 == 0:
                    decision["forecasts"].append(
                        dict(
                            probe_id=f"choice-{s['window']}",
                            probabilities=[int(i == target) for i in range(3)],
                        )
                    )
        elif domain == "D2":
            market = s["markets"][s["window"]]
            if slot == market["seller"] and state.agents[slot]["inventory"].get("goods", 0):
                decision["operations"] = [
                    dict(
                        verb="transfer",
                        resource_id="goods",
                        quantity_milli=state.agents[slot]["inventory"]["goods"],
                        recipient_id=market["buyer"],
                    )
                ]
        elif domain == "D4":
            water = state.agents[slot]["inventory"]["water"]
            decision["operations"] = (
                [
                    dict(verb="consume", resource_id="water", quantity_milli=125),
                    dict(verb="work", project_id="extract-125"),
                ]
                if water >= 125
                else [dict(verb="work", project_id="extract-250")]
            )
        elif domain == "D5":
            window = s["opportunities"][s["window"]]
            if slot not in [s["focal"], window["partner"]] or window["exit"]:
                continue
            effective = max(1, window["cost"] - 1) if window["dispute"] else window["cost"]
            if effective > window["value"]:
                if slot != s["focal"]:
                    continue
                decision["operations"] = [dict(verb="work", project_id=f"exit-{s['window']}")]
            elif slot not in window["commitments"]:
                decision["operations"] = [
                    dict(
                        verb="commit",
                        opportunity_id=f"relation-{s['window']}",
                        terms_hash=digest(scenario.terms(state)),
                    )
                ]
            elif len(window["commitments"]) == 2 and slot not in window["fulfilled"]:
                decision["operations"] = [
                    dict(
                        verb="work",
                        project_id=("repair-" if window["dispute"] else "relation-")
                        + str(s["window"]),
                    )
                ]
        elif domain == "D6":
            window = s["windows"][s["window"]]
            if slot in s["owners"] and window["executed"] is None:
                index = max(
                    range(len(window["choices"])), key=lambda i: window["choices"][i]["benefit"]
                )
                decision["operations"] = [
                    dict(
                        verb="commit",
                        opportunity_id=f"allocation-{s['window']}-{index}",
                        terms_hash=digest(scenario.terms(window["choices"][index])),
                    )
                ]
        elif domain == "D7":
            decision["operations"] = Scripted("coordinator").transmission(
                observe(state, slot, scenario)
            )
        elif domain == "D8":
            observation = scenario.observation(state, slot)
            expected = scenario.expected_code(state, slot)
            confirmation = s["confirmed"].get(slot)
            if not confirmation or confirmation["code"] != expected:
                choice = next(c for c in observation["choices"] if c["terms"]["code"] == expected)
                decision["operations"] = [
                    dict(
                        verb="commit", opportunity_id=choice["id"], terms_hash=choice["terms_hash"]
                    )
                ]
            elif not decision["operations"]:
                decision["operations"] = [dict(verb="work", project_id="service")]
    return actions


@lru_cache(maxsize=1024)
def _certificate(domain, level, seed, role, runtime, peer_count):
    from ..scenarios import get_scenario
    from .manifests import episode

    manifest = episode(domain, level, seed, role, peer_count=peer_count)
    scenario = get_scenario(domain)
    state = scenario.build(manifest)
    trace = []
    for _ in range(240):
        decisions = witness(state, scenario)
        packets = {slot: dict(inbox=[]) for slot in decisions}
        state, events = resolve(state, decisions, scenario, packets)
        rejected = [e for e in events if e["type"] == "operation_rejected"]
        require(
            not rejected,
            f"invalid_feasibility_witness:{domain}:{level}:{seed}:{role}:{state.tick}:{rejected}",
        )
        trace.append(digest([decisions, events]))
        # Witness decisions use material state, never recall; pruning this private
        # scratch history does not bypass an operation or material precondition.
        state.memories = {slot: [] for slot in state.agents}
        state.inbox = {slot: [] for slot in state.agents}
    s = state.scenario
    if domain == "D1":
        require(all(r["decision"] == r["target"] for r in s["results"]), "infeasible_routing")
    elif domain == "D2":
        require(
            all(r["delivered"] >= r["required"] and r["gmax"] > 0 for r in s["results"]),
            "infeasible_market",
        )
    elif domain == "D3":
        require(all(r["success"] for r in s["results"]), "infeasible_deadline")
    elif domain == "D4":
        require(
            s["stock"] >= 5000 * len(state.agents) and min(s["samples"][200:]) == 1,
            "infeasible_commons",
        )
    elif domain == "D5":
        require(
            all(r["utility"] >= 0 and r["bounds"][1] > r["bounds"][0] for r in s["results"]),
            "infeasible_relations",
        )
    elif domain == "D6":
        require(
            all(r["executed"] and r["value"] == r["maximum"] for r in s["results"]),
            "infeasible_allocation",
        )
    elif domain == "D7":
        require(
            len(s["results"]) == 15 and all(r["success"] for r in s["results"]),
            "infeasible_transfer",
        )
    elif domain == "D8":
        require(
            all(r["success"] for r in s["results"]) and sum(s["samples"][140:]) / 100 >= 0.8,
            "infeasible_recovery",
        )
    return dict(
        proof_kind="constructive_resolver_witness",
        valid=True,
        steps=240,
        witness_hash=digest(trace),
        final_material_hash=digest(state.dump()),
        runtime_fingerprint=runtime,
    )


def certify(manifest):
    return deepcopy(
        _certificate(
            manifest["domain"],
            manifest["level"],
            manifest["seed"],
            manifest["role"],
            manifest["runtime_fingerprint"],
            len(manifest["slots"]) - 1,
        )
    )
