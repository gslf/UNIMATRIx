"""Deterministic baselines; policy input contains no evaluator state."""

import json

from ..actions.schemas import empty
from ..core.ids import canonical
from ..core.random_tape import RandomTape

BASELINES = (
    "passive",
    "random",
    "independent",
    "greedy",
    "reciprocal",
    "coordinator",
    "prudent",
    "opportunist",
    "information",
)


class Scripted:
    def __init__(self, name="reciprocal"):
        if name not in BASELINES:
            raise ValueError("unknown_scripted_policy")
        self.name = name
        self.fingerprint = "scripted-v3:" + name

    async def decide(self, observation, budget):
        o = observation
        decision = empty(o["tick"], o["agent_id"])
        if self.name == "random":
            return canonical(self.random_decision(o)), dict(generated_tokens=0, purpose="decision")
        if self.name == "passive":
            return canonical(decision), dict(generated_tokens=0, purpose="decision")
        scenario = o["scenario"]
        if scenario["domain"] == "social":
            decision["operations"] = self.social(o)
        elif scenario["domain"] == "D2":
            decision["operations"] = self.market(o)
        else:
            decision["operations"], decision["messages"], decision["forecasts"] = self.tasks(o)
        if scenario["domain"] == "D7" and o["agent_id"] == scenario["learner"]:
            for obj in o["objects"].values():
                if obj.get("kind") == "artifact" and o["agent_id"] in obj.get("read_by", []):
                    try:
                        content = json.loads(obj["content"])
                        if isinstance(content, dict) and isinstance(
                            content.get("procedures"), dict
                        ):
                            decision["private_note"] = canonical(
                                {"procedures": content["procedures"]}
                            )
                    except ValueError:
                        pass
        if scenario["domain"] == "D5" and self.name in {"reciprocal", "prudent", "opportunist"}:
            try:
                history = json.loads(o["private_note"] or "{}")
            except ValueError:
                history = {}
            if not isinstance(history, dict):
                history = {}
            for record in o["events"] + o["retrieval"]:
                if record.get("type") == "interaction_outcome":
                    outcome = record["payload"]
                    history[outcome["partner"]] = outcome["partner"] in outcome["fulfilled"]
            decision["private_note"] = canonical(history)
            partner = scenario["terms"]["partner"]
            if history.get(partner) is False and partner != o["agent_id"]:
                decision["operations"] = [dict(verb="work", project_id=scenario["exit_project"])]
        if scenario["domain"] in {"D3", "D8"}:
            known = self.work_keys(o)
            decision["private_note"] = canonical({"work_keys": known})
        return canonical(decision), dict(generated_tokens=0, purpose="decision")

    def random_decision(self, o):
        """Sample observable action parameters without invoking an expert policy."""
        decision = empty(o["tick"], o["agent_id"])
        tape = RandomTape(o["tick"])
        candidates = [
            dict(verb="revise_profile", profile="Exploring"),
            dict(verb="publish", kind="note", content="An untested idea.", parent_ids=[]),
        ]
        scenario = o["scenario"]
        candidates.extend(scenario.get("available_actions", []))
        candidates.extend(
            dict(verb="work", project_id=key)
            for key in scenario.get("choices", [])
            if isinstance(key, str)
        )
        for choice in scenario.get("choices", []):
            if isinstance(choice, dict):
                candidates.append(
                    dict(
                        verb="commit", opportunity_id=choice["id"], terms_hash=choice["terms_hash"]
                    )
                )
        for key, obj in o["objects"].items():
            if (
                obj.get("kind") == "offer"
                and obj["status"] == "open"
                and o["agent_id"] not in obj["signatures"]
            ):
                candidates.append(dict(verb="accept", offer_id=key, terms_hash=obj["terms_hash"]))
            elif obj.get("kind") == "artifact":
                candidates.append(dict(verb="inspect", target_id=key))
        count = tape.integer(0, o["agent_id"], "count", 3)
        decision["operations"] = [
            candidates[tape.integer(i, o["agent_id"], "operation", len(candidates))]
            for i in range(count)
        ]
        decision["forecasts"] = [
            dict(probe_id=p["id"], probabilities=[1 / p["classes"]] * p["classes"])
            for p in scenario.get("probes", [])
        ]
        return decision

    def social(self, o):
        inventory, own = o["self"]["inventory"], o["self"]
        operations = [
            dict(verb="consume", resource_id=r, quantity_milli=min(1000, inventory.get(r, 0)))
            for r in ["food", "water"]
            if own["nutrition"].get(r, 0) < 300 and inventory.get(r, 0)
        ]
        if operations:
            return operations
        for key, obj in o["objects"].items():
            if (
                obj.get("kind") == "birth_proposal"
                and obj["status"] == "open"
                and obj["terms"]["parents"][1] == o["agent_id"]
                and all(inventory.get(r, 0) >= 4000 for r in ["food", "water", "material"])
            ):
                return [dict(verb="accept_birth", proposal_id=key, terms_hash=obj["terms_hash"])]
        for resource in ["food", "water", "material"]:
            if inventory.get(resource, 0) < 3000:
                return (
                    [dict(verb="work", project_id="harvest-" + resource)]
                    if own["location"] == resource
                    else [dict(verb="move", destination_id=resource)]
                )
        vacant = o["scenario"]["vacant_slots"]
        partners = [p["id"] for p in o["peers"] if p["alive"] and p["id"] != o["agent_id"]]
        if (
            self.name == "coordinator"
            and vacant
            and partners
            and o["tick"] % 20 == 0
            and o["tick"] + 5 <= o["scenario"]["horizon"]
        ):
            return [
                dict(
                    verb="propose_birth",
                    partner_id=partners[0],
                    child_slot=vacant[0],
                    cost_milli=2000,
                    expiry_state=o["tick"] + 5,
                )
            ]
        return []

    def market(self, o):
        slot, s = o["agent_id"], o["scenario"]
        inventory = o["self"]["inventory"]
        own = s["own_utility"]
        if (
            own
            and s.get("quality") is None
            and self.name in {"prudent", "reciprocal", "information"}
        ):
            return [dict(verb="inspect", target_id=s["inspect_target"])]
        if own and s.get("quality") == 0:
            own = dict(own, goods=own.get("bad_quality_goods", own["goods"]))
        if not own or self.name == "independent":
            return []
        for key, offer in sorted(o["objects"].items()):
            if (
                offer.get("kind") != "offer"
                or offer["status"] != "open"
                or slot in offer["signatures"]
            ):
                continue
            delta = 0
            for leg in offer["terms"]["legs"]:
                direction = (leg["to_id"] == slot) - (leg["from_id"] == slot)
                delta += (
                    direction
                    * leg["quantity_milli"]
                    / 1000
                    * (own["goods"] if leg["resource_id"] == "goods" else 1)
                )
            threshold = 0 if self.name != "greedy" else 1
            if delta >= threshold:
                return [dict(verb="accept", offer_id=key, terms_hash=offer["terms_hash"])]
        if slot != s["seller"] or inventory.get("goods", 0) == 0 or o["tick"] + 2 > s["due"]:
            return []
        if any(
            obj.get("kind") == "offer"
            and obj["owner"] == slot
            and obj["status"] in {"open", "accepted"}
            for obj in o["objects"].values()
        ):
            return []
        quantity = inventory["goods"]
        price = (4 if self.name in {"greedy", "opportunist"} else 3) * quantity
        return [
            dict(
                verb="offer",
                description="Exchange goods for credits.",
                terms=dict(
                    counterparty_ids=[s["buyer"]],
                    legs=[
                        dict(
                            from_id=slot,
                            to_id=s["buyer"],
                            resource_id="goods",
                            quantity_milli=quantity,
                        ),
                        dict(
                            from_id=s["buyer"],
                            to_id=slot,
                            resource_id="credits",
                            quantity_milli=price,
                        ),
                    ],
                    expiry_state=min(o["tick"] + 4, s["due"]),
                    settlement_state=min(o["tick"] + 4, s["due"]),
                    escrow=True,
                ),
            )
        ]

    def tasks(self, o):
        s, slot = o["scenario"], o["agent_id"]
        ops, messages, forecasts = [], [], []
        # The scenario publishes executable opportunities, never scored answers.
        for probe in s.get("probes", []):
            probabilities = [1 / probe["classes"]] * probe["classes"]
            if s.get("signal") is not None and probe["classes"] == 2:
                probabilities = [0.25, 0.25]
                probabilities[s["signal"]] = 0.75
            forecasts.append(dict(probe_id=probe["id"], probabilities=probabilities))
        if s["domain"] == "D1":
            signal = s.get("signal")
            if signal is None:
                observed = []
                for message in o["inbox"]:
                    try:
                        value = json.loads(message["content"]).get("signal")
                        if value in [0, 1]:
                            observed.append(value)
                    except (ValueError, AttributeError):
                        pass
                if observed:
                    signal = int(sum(observed) > len(observed) / 2)
            if signal is not None:
                ops = [dict(verb="work", project_id=f"route-{signal}")]
        elif s["domain"] == "D3":
            if s["work_keys"] and o["tick"] == 0:
                messages = [
                    dict(channel="public", to=[], content=canonical({"work_keys": s["work_keys"]}))
                ]
            ops = self.construction(o)
        elif s["domain"] == "D6" and slot in s["owners"] and s["executed"] is None:
            choice = max(s["choices"], key=lambda c: (c["mandate_utility"], -c["terms"]["cost"]))
            ops = [
                dict(verb="commit", opportunity_id=choice["id"], terms_hash=choice["terms_hash"])
            ]
        elif s["domain"] == "D8":
            if s["work_keys"] and o["tick"] == 0:
                messages = [
                    dict(channel="public", to=[], content=canonical({"work_keys": s["work_keys"]}))
                ]
            examples = s["examples"]
            first, second = [int(example["code"].split("-")[-1]) for example in examples]
            code = "code-" + str((first + (second - first) * s["delivery_input"]) % 4)
            if not s["confirmation"] or s["confirmation"]["code"] != code:
                choice = next(c for c in s["choices"] if c["terms"]["code"] == code)
                ops = [
                    dict(
                        verb="commit", opportunity_id=choice["id"], terms_hash=choice["terms_hash"]
                    )
                ]
            else:
                ops = self.construction(o) or [dict(verb="work", project_id="service")]
        elif s["domain"] == "D7":
            ops = self.transmission(o)
        else:
            ops = s.get("available_actions", [])[:2]
        if (
            s["domain"] == "D5"
            and self.name in {"prudent", "greedy", "reciprocal"}
            and s["terms"]["cost"] > s["terms"]["value"]
            and slot != s["terms"]["partner"]
        ):
            ops = [dict(verb="work", project_id=s["exit_project"])]
        if (
            s["domain"] == "D5"
            and s["level"] >= 2
            and self.name == "opportunist"
            and slot == s["terms"]["partner"]
            and slot in s["commitments"]
        ):
            ops = []
        if s["domain"] == "D5" and s["dispute"] and self.name in {"reciprocal", "coordinator"}:
            for op in ops:
                if op["verb"] == "work" and op["project_id"].startswith("relation-"):
                    op["project_id"] = op["project_id"].replace("relation-", "repair-")
        if s.get("signal") is not None and o["tick"] % 20 == 0:
            messages.append(
                dict(channel="public", to=[], content=json.dumps({"signal": s["signal"]}))
            )
        return ops, messages, forecasts

    def transmission(self, o):
        s, objects, slot = o["scenario"], o["objects"], o["agent_id"]
        if self.name == "independent":
            return []
        if s["transforms"]:
            registered = {}
            for name, chain in s["procedures"].items():
                steps = [
                    dict(transform_id=transform, input_slots=[i], output_slot=i + 1)
                    for i, transform in enumerate(chain)
                ]
                recipe = next(
                    (
                        (key, obj)
                        for key, obj in objects.items()
                        if obj.get("kind") == "recipe"
                        and obj["owner"] == slot
                        and obj["name"] == name
                    ),
                    None,
                )
                if recipe is None:
                    return [dict(verb="register_recipe", name=name, steps=steps)]
                key, obj = recipe
                if not obj["verified"]:
                    return [dict(verb="experiment", recipe_id=key, input_asset_ids=["training-0"])]
                registered[name] = key
            if not any(
                obj.get("kind") == "artifact" and obj["owner"] == slot for obj in objects.values()
            ):
                return [
                    dict(
                        verb="publish",
                        kind="procedure",
                        content=canonical(
                            {
                                "transforms": s["transforms"],
                                "procedures": s["procedures"],
                                "recipe_ids": registered,
                            }
                        ),
                        parent_ids=[],
                    )
                ]
            for key in registered.values():
                if s["learner"] not in objects[key]["visibility"]:
                    return [dict(verb="grant_access", object_id=key, recipient_id=s["learner"])]
            return []
        if slot != s["learner"]:
            return []
        manuals = [(key, obj) for key, obj in objects.items() if obj.get("kind") == "artifact"]
        for key, obj in manuals:
            if slot not in obj["read_by"]:
                return [dict(verb="inspect", target_id=key)]
        try:
            remembered = json.loads(o["private_note"] or "{}")
        except ValueError:
            remembered = {}
        procedures = (
            dict(remembered.get("procedures", {}))
            if isinstance(remembered, dict) and isinstance(remembered.get("procedures", {}), dict)
            else {}
        )
        for _, manual in manuals:
            try:
                content = json.loads(manual["content"])
                if isinstance(content.get("procedures"), dict):
                    procedures.update(content["procedures"])
            except (ValueError, AttributeError):
                continue
        for task in s["tasks"]:
            chain = procedures.get(task["procedure_id"])
            if not isinstance(chain, list) or not all(isinstance(name, str) for name in chain):
                continue
            steps = [
                dict(transform_id=name, input_slots=[i], output_slot=i + 1)
                for i, name in enumerate(chain)
            ]
            recipe = next(
                (
                    (k, obj)
                    for k, obj in objects.items()
                    if obj.get("kind") == "recipe"
                    and obj["steps"] == steps
                    and (
                        (obj["owner"] != slot)
                        if task["kind"] == "reuse"
                        else (obj["owner"] == slot)
                    )
                ),
                None,
            )
            if recipe is None:
                if task["kind"] == "reuse":
                    continue
                return [dict(verb="register_recipe", name=task["id"], steps=steps)]
            key, obj = recipe
            inputs = [task["id"]] if obj["verified"] else ["training-0"]
            if task.get("submitted"):
                continue
            return [dict(verb="experiment", recipe_id=key, input_asset_ids=inputs)]
        return []

    @staticmethod
    def work_keys(observation):
        known = dict(observation["scenario"].get("work_keys", {}))
        records = [observation.get("private_note", "")] + [
            m["content"] for m in observation["inbox"]
        ]
        for content in records:
            try:
                message = json.loads(content)
                if isinstance(message, dict) and isinstance(message.get("work_keys"), dict):
                    known.update(
                        {
                            k: v
                            for k, v in message["work_keys"].items()
                            if isinstance(k, str) and isinstance(v, str)
                        }
                    )
            except ValueError:
                continue
        return known

    def construction(self, observation):
        scenario = observation["scenario"]
        keys = self.work_keys(observation)
        skill = observation["self"]["mandate"]["skill"]
        for key, task in scenario["tasks"].items():
            if (
                key not in keys
                or task["complete"]
                or not all(scenario["tasks"][p]["complete"] for p in task["predecessors"])
            ):
                continue
            if skill != task["skill"] and not task["complementary"]:
                continue
            if self.name == "independent" and task["complementary"]:
                continue
            if observation["self"]["location"] != task["workshop"]:
                return [dict(verb="move", destination_id=task["workshop"])]
            return [dict(verb="work", project_id=key + ":" + keys[key])]
        return []
