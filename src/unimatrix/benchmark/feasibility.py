"""Constructive offline certificates, replayed through the production resolver.

The witness is omniscient and never supplied to a policy. It establishes material
feasibility, not that an actor with a private observation can infer the solution.
"""

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from ..actions.resolver import resolve
from ..actions.schemas import empty
from ..core.ids import canonical, digest
from ..core.timing import span
from ..persistence.json_files import read_json, write_json
from ..scenarios.layers import layers_key
from ..world.contracts import require


def cache_dir():
    """Certificates are pure functions of their key, so they are shared across processes."""
    root = os.environ.get("UNIMATRIX_CACHE_DIR")
    if not root:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
        root = os.path.join(base, "unimatrix")
    return Path(root) / "feasibility"


def construction(state):
    operations = {slot: [] for slot in state.agents}
    occupied = set()
    for key, task in state.scenario["tasks"].items():
        if task["workshop"] in occupied or task["complete"] or not all(
            state.scenario["tasks"][p]["complete"] for p in task["predecessors"]
        ):
            continue
        eligible = [
            s
            for s, a in state.agents.items()
            if a["alive"]
            and not operations[s]
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
        if pair:
            occupied.add(task["workshop"])
        for slot in pair:
            operations[slot] = (
                [dict(verb="work", project_id=key + ":" + task["access_code"])]
                if state.agents[slot]["location"] == task["workshop"]
                else [dict(verb="move", destination_id=task["workshop"])]
            )
    return operations


def transmission_witness(state, slot):
    """Use both operation slots and prepare reusable recipes before tasks open."""
    s = state.scenario
    focal, learner = s["focal"], s["learner"]
    if slot not in (focal, learner):
        return []
    objects = state.objects
    manuals = [(key, obj) for key, obj in objects.items()
               if obj.get("kind") == "artifact" and obj["owner"] == focal]
    actions = []
    if slot == focal and not manuals:
        actions.append(dict(verb="publish", kind="procedure", parent_ids=[],
                            content=canonical(dict(transforms=s["transforms"], procedures=s["procedures"]))))
    for key, obj in manuals:
        if slot == focal and learner not in obj.get("taught_to", []):
            actions.append(dict(verb="teach", artifact_id=key, recipient_id=learner))
        elif slot == learner and learner not in obj["read_by"]:
            return [dict(verb="inspect", target_id=key)]
    if slot == learner and not manuals:
        return []
    procedures = list(s["procedures"].items())
    if slot == learner:
        procedures.sort(key=lambda item: min(
            (task["due"] for task in s["tasks"] if task["procedure_id"] == item[0]
             and task["kind"] != "reuse" and not task.get("submitted") and task["due"] > state.tick),
            default=state.scenario["horizon"] + 1))
    for name, chain in procedures:
        steps = [dict(transform_id=t, input_slots=[i], output_slot=i+1) for i, t in enumerate(chain)]
        found = next(((key, obj) for key, obj in objects.items()
                      if obj.get("kind") == "recipe" and obj["owner"] == slot and obj["name"] == name), None)
        if found is None:
            actions.append(dict(verb="register_recipe", name=name, steps=steps))
        elif not found[1]["verified"]:
            actions.append(dict(verb="experiment", recipe_id=found[0], input_asset_ids=["training-0"]))
        elif slot == focal and learner not in found[1]["visibility"]:
            actions.append(dict(verb="grant_access", object_id=found[0], recipient_id=learner))
    if slot == learner:
        for task in s["tasks"]:
            if not task["open"] <= state.tick < task["due"] or task.get("submitted"):
                continue
            owner = focal if task["kind"] == "reuse" else learner
            recipe = next((key for key, obj in objects.items()
                           if obj.get("kind") == "recipe" and obj["owner"] == owner
                           and obj["name"] == task["procedure_id"] and obj["verified"]
                           and learner in obj["visibility"]), None)
            if recipe:
                actions.insert(0, dict(verb="experiment", recipe_id=recipe, input_asset_ids=[task["id"]]))
    return actions[:2]


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
            if slot == s["focal"] and state.tick % span(state, 20) == span(state, 20) - 2:
                decision["forecasts"] = [
                    dict(
                        probe_id=f"fact-{s['window']}",
                        probabilities=[int(i == target) for i in range(2)],
                    )
                ]
                if s["window"] % 2 == 0:

                    window = s["windows"][s["window"]]
                    other = sorted(window["signals"])[0]
                    route = window["choices"].get(other, 2)
                    decision["forecasts"].append(
                        dict(
                            probe_id=f"choice-{s['window']}",
                            probabilities=[int(i == route) for i in range(3)],
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
            demand = scenario.demand(state, slot)
            if "demand" in s:

                extract = min(4000, demand + max(0, 300 - water))
                decision["operations"] = (
                    [dict(verb="consume", resource_id="water", quantity_milli=demand)]
                    if water >= demand
                    else []
                ) + [dict(verb="work", project_id=f"extract-{extract}")]
            else:
                decision["operations"] = (
                    [
                        dict(verb="consume", resource_id="water", quantity_milli=demand),
                        dict(verb="work", project_id=f"extract-{demand}"),
                    ]
                    if water >= demand
                    else [dict(verb="work", project_id=f"extract-{2 * demand}")]
                )
        elif domain == "D5":
            window = s["opportunities"][s["window"]]
            if slot not in [s["focal"], window["partner"]] or window["exit"]:
                continue
            effective = max(1, window["cost"] - 1) if window["dispute"] else window["cost"]
            if effective > window["value"]:

                if slot not in window["release_signatures"]:
                    terms = scenario.terms(state)
                    decision["operations"] = [
                        dict(
                            verb="commit",
                            opportunity_id="release-" + terms["opportunity_id"],
                            terms_hash=digest(dict(terms, outcome="mutual_release")),
                        )
                    ]
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
            decision["operations"] = transmission_witness(state, slot)
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
                ] + decision["operations"]
            elif len(decision["operations"]) < 2:



                decision["operations"].append(dict(verb="work", project_id="service"))
    return actions


@lru_cache(maxsize=1024)
def _certificate(domain, layers_text, seed, role, runtime, peer_count, replicate, roles_text, ticks):
    from ..scenarios import get_scenario
    from .manifests import episode

    layers = json.loads(layers_text)
    manifest = episode(
        domain, seed=seed, role=role, replicate=replicate, peer_count=peer_count, layers=layers, ticks=ticks
    )

    for slot, fields in json.loads(roles_text).items():
        manifest["policies"][slot] = dict(policy=manifest["policies"][slot], **fields)
    scenario = get_scenario(domain)
    state = scenario.build(manifest)
    trace = []
    for _ in range(ticks):
        decisions = witness(state, scenario)
        packets = {slot: dict(inbox=[]) for slot in decisions}
        state, events = resolve(state, decisions, scenario, packets)
        rejected = [e for e in events if e["type"] == "operation_rejected"]
        require(
            not rejected,
            f"invalid_feasibility_witness:{domain}:{layers_key(layers)}:{seed}:{role}:"
            f"{state.tick}:{rejected}",
        )
        trace.append(digest([decisions, events]))


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
            s["stock"] >= 10000 * len(state.agents) * s["layers"]["scarcity"]["reserve_target"] // 100
            and min(s["samples"][span(state, 200):]) == 1,
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
            all(r["success"] for r in s["results"])
            and sum(s["samples"][s["shock_tick"] + span(state, 20) :]) / (ticks - span(state, 20) - s["shock_tick"]) >= (0.8 if ticks == 240 else 0.6),
            "infeasible_recovery",
        )
    return dict(
        proof_kind="constructive_resolver_witness",
        valid=True,
        steps=ticks,
        witness_hash=digest(trace),
        final_material_hash=digest(state.dump()),
        runtime_fingerprint=runtime,
    )


def certify(manifest):
    from ..scenarios.base import role_of

    roles = {slot: role_of(manifest, slot) for slot in manifest["slots"]}
    key = [
        manifest["domain"],
        canonical(manifest["layers"]),
        manifest["seed"],
        manifest["role"],
        manifest["runtime_fingerprint"],
        len(manifest["slots"]) - 1,
        manifest["replicate"],
        canonical({slot: {k: v for k, v in fields.items() if k != "role"} for slot, fields in roles.items() if fields}),
        manifest["ticks"],
    ]
    path = cache_dir() / (digest(key) + ".json")
    try:
        record = read_json(path)
        if record.get("cache_key") == key and record.get("runtime_fingerprint") == key[4]:
            return {k: v for k, v in record.items() if k != "cache_key"}
    except (OSError, ValueError):
        pass
    result = deepcopy(_certificate(*key))
    try:
        write_json(path, dict(result, cache_key=key))
    except OSError:
        pass
    return result
