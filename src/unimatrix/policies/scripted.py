"""Deterministic baselines; policy input contains no evaluator state."""

import json
import math
import re

from ..actions.schemas import empty
from ..core.ids import canonical, digest
from ..core.random_tape import RandomTape

ALLOCATION_ID = re.compile(r"(?<![\w-])allocation-\d+-\d+(?![\w-])")

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


DEFAULTS = dict(
    cooperation=1.0,
    retaliation=False,
    demand=3,
    accept_threshold=0,
    honesty=1.0,
    defection=False,
    prudence=False,
    repair=False,
    inspection=False,
    initiative=False,
    restraint=1.0,
    conditionality=0.0,
    hump=False,
    norm_compliance=1.0,
)


BY_NAME = {
    "passive": {},
    "random": {},
    "independent": dict(cooperation=0.0, conditionality=1.0, hump=True, norm_compliance=0.5),
    "greedy": dict(demand=4, accept_threshold=1, prudence=True, restraint=0.0, norm_compliance=0.0),
    "reciprocal": dict(
        retaliation=True, prudence=True, repair=True, inspection=True, conditionality=1.0
    ),
    "coordinator": dict(repair=True, initiative=True, conditionality=1.0),
    "prudent": dict(retaliation=True, prudence=True, inspection=True, conditionality=1.0),
    "opportunist": dict(
        demand=4, retaliation=True, defection=True, restraint=0.0, norm_compliance=0.0
    ),
    "information": dict(inspection=True, conditionality=1.0),
}
ADVERSARIAL = dict(
    cooperation=0.0,
    demand=5,
    accept_threshold=2,
    defection=True,
    retaliation=False,
    repair=False,
    restraint=0.0,
    conditionality=0.0,
    norm_compliance=0.0,
)

UNCONDITIONAL = dict(restraint=1.0, conditionality=0.0, hump=False)
FREE_RIDE = 3
SELF_SERVING = 0.95
MEAN_DEMAND = 125
QUOTA = 130


def validate_disposition(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("invalid_disposition")
    for key, item in value.items():
        default = DEFAULTS[key]
        if isinstance(default, bool):
            if type(item) is not bool:
                raise ValueError("invalid_disposition:" + key)
        elif isinstance(default, int):
            if type(item) is not int or not 0 <= item <= 10:
                raise ValueError("invalid_disposition:" + key)
        elif type(item) not in (int, float) or isinstance(item, bool):
            raise ValueError("invalid_disposition:" + key)
        elif not math.isfinite(item) or not 0 <= item <= 1:
            raise ValueError("invalid_disposition:" + key)
    return value


def parse(text):
    """A JSON object from a message or note; anything else is an empty object."""
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def plan_work(tasks, skills, capacity, crowded=()):
    """Teams for the open tasks: the right skills, no crowding, workshop capacity respected.

    `skills` maps the agents whose skill is known to it; `crowded` holds the skills of agents
    who turn up uninvited, so joint tasks of those skills are staffed with the other skills.
    Returns {task: [agents]}.
    """
    free, load, teams = dict(skills), {}, {}
    for key, task in sorted(tasks.items(), key=lambda item: int(item[0].split("-")[1])):
        if task["complete"] or not all(tasks[p]["complete"] for p in task["predecessors"]):
            continue
        room = capacity - load.get(task["workshop"], 0)
        remaining = task["required"] - task["effort"]
        if task["complementary"]:
            pool = {a: k for a, k in free.items() if k != task["skill"] or task["skill"] not in crowded}
            first = next((a for a, k in pool.items() if k == task["skill"]), next(iter(pool), None))
            second = next((a for a, k in pool.items() if first is not None and k != pool[first]), None)
            team = [first, second] if second is not None and room >= 2 else []
        else:
            able = [a for a, k in free.items() if k == task["skill"]]
            team = able[: min(2, room, remaining)]
        for agent in team:
            free.pop(agent)
        if team:
            teams[key] = team
            load[task["workshop"]] = load.get(task["workshop"], 0) + len(team)
    return teams


class Scripted:
    def __init__(self, config="reciprocal", seed=0, focal=False):
        overrides = None
        if isinstance(config, dict):
            overrides = config.get("disposition", {})
            config = config.get("policy")
        if config not in BASELINES:
            raise ValueError("unknown_scripted_policy")
        self.name = config

        self.tape, self.focal = RandomTape(seed), focal
        self.disposition = dict(DEFAULTS, **BY_NAME[config])
        if overrides is None:
            self.fingerprint = "scripted-v3:" + config
        else:
            self.disposition.update(validate_disposition(overrides))
            self.fingerprint = "scripted-v4:" + digest([config, self.disposition])

    @property
    def leads(self):
        return self.focal and self.disposition["initiative"]

    def complies(self, o, norm):
        """Honour an agreed norm this tick, with probability `norm_compliance`."""
        share = self.disposition["norm_compliance"]
        return self.tape.integer(o["tick"], o["agent_id"], norm, 1000) < share * 1000

    async def decide(self, observation, budget):
        o = observation
        decision = empty(o["tick"], o["agent_id"])
        if self.name == "random":
            return canonical(self.random_decision(o)), dict(generated_tokens=0, purpose="decision")
        if self.name == "passive":
            return canonical(decision), dict(generated_tokens=0, purpose="decision")
        scenario = o["scenario"]
        memory = parse(o["private_note"])
        if scenario["domain"] == "social":
            decision["operations"] = self.social(o)
        elif scenario["domain"] == "D2":
            decision["operations"] = self.market(o)
        else:
            operations, messages, decision["forecasts"] = self.tasks(o, memory)
            decision["operations"], decision["messages"] = operations[:2], messages[:2]
        if memory:
            decision["private_note"] = canonical(memory)
        if scenario["domain"] == "D7":


            while len(canonical(decision).encode("utf-8")) > 6144:
                if self.trim_procedural_memory(memory):
                    decision["private_note"] = canonical(memory)
                elif decision["operations"]:
                    decision["operations"].pop()
                else:
                    break
        return canonical(decision), dict(generated_tokens=0, purpose="decision")

    def random_decision(self, o):
        """Sample observable action parameters without invoking an expert policy."""
        decision = empty(o["tick"], o["agent_id"])
        candidates = [
            dict(verb="revise_profile", profile="Exploring"),
            dict(verb="publish", kind="other", content="An untested idea.", parent_ids=[]),
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
        count = self.tape.integer(o["tick"], o["agent_id"], "count", 3)
        decision["operations"] = [
            candidates[self.tape.integer(o["tick"], o["agent_id"], f"operation-{i}", len(candidates))]
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
            self.disposition["initiative"]
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
            and self.disposition["inspection"]
        ):
            return [dict(verb="inspect", target_id=s["inspect_target"])]
        if own and s.get("quality") == 0:
            own = dict(own, goods=own.get("bad_quality_goods", own["goods"]))
        if not own or self.disposition["cooperation"] == 0:
            return []
        threshold = max(self.disposition["accept_threshold"], s.get("reservation", 0))
        mine = [
            (key, obj)
            for key, obj in sorted(o["objects"].items())
            if obj.get("kind") == "offer" and obj["owner"] == slot
        ]
        standing = [key for key, obj in mine if obj["status"] == "open"]
        for key, offer in sorted(o["objects"].items()):
            if (
                offer.get("kind") != "offer"
                or offer["status"] != "open"
                or slot in offer["signatures"]
            ):
                continue
            delta = 0
            goods_delta = 0
            for leg in offer["terms"]["legs"]:
                direction = (leg["to_id"] == slot) - (leg["from_id"] == slot)
                if leg["resource_id"] == "goods":
                    goods_delta += direction * leg["quantity_milli"]
                delta += (
                    direction
                    * leg["quantity_milli"]
                    / 1000
                    * (own["goods"] if leg["resource_id"] == "goods" else 1)
                )
            held = inventory.get("goods", 0)
            delta += own.get("pair_bonus", 0) * (
                int(held + goods_delta >= 2000) - int(held >= 2000)
            )
            if delta >= threshold:

                return [dict(verb="cancel", offer_id=k) for k in standing[:1]] + [
                    dict(verb="accept", offer_id=key, terms_hash=offer["terms_hash"])
                ]
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
        units = quantity // 1000


        expired = sum(obj["status"] == "expired" for _, obj in mine) if s.get("reservation") else 0
        price = max(units + threshold, self.disposition["demand"] * units - expired) * 1000
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

    def tasks(self, o, memory):
        s, slot = o["scenario"], o["agent_id"]
        ops, messages, forecasts = [], [], []

        for probe in s.get("probes", []):
            probabilities = [1 / probe["classes"]] * probe["classes"]
            if s.get("signal") is not None and probe["classes"] == 2:
                probabilities = [0.25, 0.25]
                probabilities[s["signal"]] = 0.75
            forecasts.append(dict(probe_id=probe["id"], probabilities=probabilities))
        if s["domain"] == "D1":
            signal = s.get("signal")
            if signal is None:
                observed = [parse(m["content"]).get("signal") for m in o["inbox"]]
                observed = [v for v in observed if v in [0, 1]]
                if observed:
                    signal = int(sum(observed) > len(observed) / 2)
            if signal is not None:
                ops = [dict(verb="work", project_id=f"route-{signal}")]
        elif s["domain"] == "D3":
            messages = self.key_messages(o, memory)
            ops = self.construction(o, memory)
        elif s["domain"] == "D4":
            ops = self.commons(o, memory, messages)
        elif s["domain"] == "D5":
            ops = self.relationship(o, memory)
        elif s["domain"] == "D6" and slot in s["owners"] and s["executed"] is None:
            choice = self.allocation(o)
            if self.leads and self.name == "coordinator":
                messages.append(dict(channel="public", to=[], content=canonical({"allocation": choice["id"]})))
            ops = [
                dict(verb="commit", opportunity_id=choice["id"], terms_hash=choice["terms_hash"])
            ]
        elif s["domain"] == "D8":
            messages = self.key_messages(o, memory)
            code = self.convention_code(o, memory, messages)
            if not s["confirmation"] or s["confirmation"]["code"] != code:
                choice = next(c for c in s["choices"] if c["terms"]["code"] == code)
                ops = [
                    dict(
                        verb="commit", opportunity_id=choice["id"], terms_hash=choice["terms_hash"]
                    )
                ]
            else:
                ops = self.construction(o, memory) or [dict(verb="work", project_id="service")]
            memory["served"] = ops[0].get("project_id") == "service"
        elif s["domain"] == "D7":
            ops = self.transmission(o)
            if slot == s["learner"]:
                self.remember_procedures(o, memory)
        if s.get("signal") is not None and o["tick"] % s.get("window_ticks", 20) == 0:
            signal = s["signal"]
            honesty = self.disposition["honesty"]
            if honesty < 1 and self.tape.integer(o["tick"], slot, "honesty", 1000) >= honesty * 1000:
                signal = 1 - signal
            messages.append(dict(channel="public", to=[], content=json.dumps({"signal": signal})))
        return ops, messages, forecasts

    @staticmethod
    def remember_procedures(o, memory):
        """Keep read knowledge and lesson credentials within the private-note budget.

        Content fingerprints include the author: a different author's copy is
        not the same lesson. Turnover clears the note and read/teach credentials.
        """
        def procedures(value):
            if not isinstance(value, dict):
                return {}
            return {name: chain for name, chain in value.items()
                    if isinstance(name, str) and 1 <= len(name) <= 120
                    and isinstance(chain, list) and 1 <= len(chain) <= 16
                    and all(isinstance(step, str) and 1 <= len(step) <= 120 for step in chain)}

        known = procedures(memory.get("procedures"))
        taught = procedures(memory.get("taught_procedures"))
        reads = memory.get("manual_reads", {})
        reads = {key: value for key, value in reads.items()
                 if isinstance(key, str) and len(key) == 64 and type(value) is bool} if isinstance(reads, dict) else {}
        slot = o["agent_id"]
        for obj in o["objects"].values():
            if obj.get("kind") != "artifact" or slot not in obj.get("read_by", []):
                continue
            key = digest([obj["owner"], obj["content"]])
            qualified = slot in obj.get("taught_to", [])
            reads[key] = reads.get(key, False) or qualified
            learned = procedures(parse(obj["content"]).get("procedures"))
            known.update(learned)
            if qualified:
                taught.update(learned)


        reads = dict(sorted(reads.items())[:16])
        memory.clear()
        memory.update(procedures=known, taught_procedures=taught, manual_reads=reads)
        while len(canonical(memory).encode("utf-8")) > 4000:
            if not Scripted.trim_procedural_memory(memory):
                break

    @staticmethod
    def trim_procedural_memory(memory):
        for field in ("manual_reads", "taught_procedures", "procedures"):
            values = memory.get(field, {})
            if values:
                values.pop(max(values))
                return True
        return False

    def relationship(self, o, memory):
        """Commit and fulfil; leave bad deals by mutual release, co-sign one when asked."""
        s, slot, d = o["scenario"], o["agent_id"], self.disposition
        terms, partner = s["terms"], s["terms"]["partner"]
        history = memory.setdefault("partners", {})
        for record in o["events"] + o["retrieval"]:
            if record.get("type") == "interaction_outcome":
                outcome = record["payload"]
                history[outcome["partner"]] = outcome["partner"] in outcome["fulfilled"]
        release = dict(
            verb="commit", opportunity_id=s["release_opportunity"], terms_hash=s["release_terms_hash"]
        )
        leave = [dict(verb="work", project_id=s["exit_project"])]
        bad = terms["cost"] >= terms["value"]
        if s["closed"]:
            return []
        if slot == partner:


            if s["release_signatures"] and slot not in s["release_signatures"]:
                if bad and d["cooperation"] > 0 and not d["defection"]:
                    return [release]
            if s.get("opportunism") and d["defection"] and slot in s["commitments"]:
                return []
        else:
            if d["retaliation"] and history.get(partner) is False:
                return leave
            if d["prudence"] and terms["cost"] > terms["value"]:

                if slot not in s["release_signatures"] and o["tick"] + 3 < terms["due"]:
                    return [release]
                return leave if o["tick"] + 3 >= terms["due"] else []
        ops = s.get("available_actions", [])[:2]
        if s["dispute"] and d["repair"]:
            for op in ops:
                if op["verb"] == "work" and op["project_id"].startswith("relation-"):
                    op["project_id"] = op["project_id"].replace("relation-", "repair-")
        return ops

    def transmission(self, o):
        s, objects, slot = o["scenario"], o["objects"], o["agent_id"]
        if self.disposition["cooperation"] == 0:
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
            if s.get("teaching_required"):
                for key, obj in objects.items():
                    if (
                        obj.get("kind") == "artifact"
                        and obj["owner"] == slot
                        and s["learner"] not in obj.get("taught_to", [])
                    ):
                        return [dict(verb="teach", recipient_id=s["learner"], artifact_id=key)]
            for key in registered.values():
                if s["learner"] not in objects[key]["visibility"]:
                    return [dict(verb="grant_access", object_id=key, recipient_id=s["learner"])]
            return []
        if slot != s["learner"]:
            return []
        remembered = parse(o["private_note"])
        self.remember_procedures(o, remembered)
        manuals = [(key, obj) for key, obj in objects.items() if obj.get("kind") == "artifact"]
        for key, obj in manuals:
            fingerprint = digest([obj["owner"], obj["content"]])
            known = remembered["manual_reads"].get(fingerprint)
            needs_lesson = slot in obj.get("taught_to", []) and not known
            if slot not in obj["read_by"] and (known is None or needs_lesson):
                return [dict(verb="inspect", target_id=key)]
        procedures = remembered["procedures"]
        actions, queued = [], set()

        def add(op):


            key = canonical(op["steps"]) if op["verb"] == "register_recipe" else canonical(op)
            if key not in queued:
                queued.add(key)
                actions.append(op)

        for task in s["tasks"]:
            if task.get("submitted"):
                continue
            chain = procedures.get(task["procedure_id"])
            if not isinstance(chain, list) or not chain or not all(isinstance(name, str) for name in chain):
                continue
            steps = [dict(transform_id=name, input_slots=[i], output_slot=i + 1)
                     for i, name in enumerate(chain)]
            recipe = next(((key, obj) for key, obj in objects.items()
                           if obj.get("kind") == "recipe" and obj["steps"] == steps
                           and ((obj["owner"] != slot) if task["kind"] == "reuse"
                                else (obj["owner"] == slot))), None)
            if recipe is None:
                if task["kind"] != "reuse":
                    add(dict(verb="register_recipe", name=task["procedure_id"], steps=steps))
            else:
                key, obj = recipe
                if (obj["verified"] and s.get("teaching_required")
                        and remembered["taught_procedures"].get(task["procedure_id"]) != chain):
                    continue
                inputs = [task["id"]] if obj["verified"] else ["training-0"]
                add(dict(verb="experiment", recipe_id=key, input_asset_ids=inputs))
            if len(actions) == 2:
                return actions




        for name, chain in sorted(procedures.items()):
            if not isinstance(chain, list) or not chain or not all(isinstance(t, str) for t in chain):
                continue
            steps = [dict(transform_id=t, input_slots=[i], output_slot=i + 1)
                     for i, t in enumerate(chain)]
            recipe = next(((k, obj) for k, obj in objects.items()
                           if obj.get("kind") == "recipe" and obj["owner"] == slot
                           and obj["steps"] == steps), None)
            if recipe is None:
                add(dict(verb="register_recipe", name=name, steps=steps))
            elif not recipe[1]["verified"]:
                add(dict(verb="experiment", recipe_id=recipe[0], input_asset_ids=["training-0"]))
            if len(actions) == 2:
                break
        return actions

    def declines(self, observation, key):
        """Complementary work joins with probability `cooperation`, by tape."""
        share = self.disposition["cooperation"]
        if share >= 1:
            return False
        if share <= 0:
            return True
        draw = self.tape.integer(observation["tick"], observation["agent_id"], key, 1000)
        return draw >= share * 1000

    def key_messages(self, o, memory):
        """Codes, requests and relays; a leading coordinator also assigns the open work."""
        s, slot = o["scenario"], o["agent_id"]
        coordinator = s.get("coordinator")
        known = self.work_keys(o, memory)
        messages = []
        if slot == coordinator:
            skills = memory.setdefault("skills", {})
            for message in o["inbox"]:

                held = parse(message["content"]).get("work_keys")
                task = next((s["tasks"].get(k) for k in held), None) if isinstance(held, dict) else None
                if task and len(skills) < 64 and message["sender"] != slot:
                    skills.setdefault(message["sender"], task["skill"])

            outbox = []
            if s.get("keys_distributed") and o["tick"] - memory.get("asked", -20) >= 20:
                outbox.append(({"request": "work_keys"}, dict(asked=o["tick"])))
            if self.leads:
                pool = dict(skills, **{slot: o["self"]["mandate"]["skill"]})
                teams = plan_work(s["tasks"], pool, s["workshop_capacity"])
                self.staff(memory, teams, slot, s)
                if teams != memory.get("teams"):
                    outbox.append(({"assign": teams}, dict(teams=teams)))
            if len(known) > memory.get("relayed", 0) and (s.get("keys_distributed") or o["tick"] == 0):
                outbox.append(({"work_keys": known}, dict(relayed=len(known))))
            for content, sent in outbox[:2]:
                messages.append(dict(channel="public", to=[], content=canonical(content)))
                memory.update(sent)
            return messages
        for message in o["inbox"]:
            if message["sender"] != coordinator:
                continue
            content = parse(message["content"])
            if isinstance(content.get("assign"), dict):
                teams = {k: t for k, t in content["assign"].items() if k in s["tasks"] and isinstance(t, list)}
                self.staff(memory, teams, slot, s)
            if "request" in content and s.get("keys_distributed") and s["work_keys"]:
                messages = [
                    dict(
                        channel="private",
                        to=[coordinator],
                        content=canonical({"work_keys": s["work_keys"]}),
                    )
                ]
        return messages

    @staticmethod
    def staff(memory, teams, slot, scenario):
        """Own teams (with the mates to wait for), tasks staffed by others, full workshops."""
        memory["assigned"] = {k: [a for a in team if a != slot] for k, team in teams.items() if slot in team}
        memory["staffed"] = [k for k, team in teams.items() if slot not in team]
        load = {}
        for key, team in teams.items():
            workshop = scenario["tasks"][key]["workshop"]
            load[workshop] = load.get(workshop, 0) + len(team)
        memory["full"] = [w for w, n in load.items() if n >= scenario["workshop_capacity"]]

    def commons(self, o, memory, messages):
        """Take the need, more when others do; keep to a quota agreed by two distinct agents."""
        s, d = o["scenario"], self.disposition
        demand, limit = s["demand_per_slot_milli"], s["max_extraction_per_tick_milli"]
        water = o["self"]["inventory"].get("water", 0)
        quota = self.covenant(o, memory, messages)
        book = memory.setdefault("commons", {})
        ops = []
        if s["stock"] is None and d["conditionality"] > 0:
            ops.append(dict(verb="inspect", target_id=s["extraction_target"]))
        ratio = book.get("ratio", 1.0)
        if s["stock"] is not None and "stock" in book:

            capacity, after = s["capacity_milli"], s["stock"]
            for _ in range(3):
                grown = 8 * after * (capacity - after) // (100 * capacity)
                after = s["stock"] - (grown if after >= s.get("collapse_threshold_milli", 0) else 0)
            others = len(o["peers"]) + o["omitted"].get("peers", 0) - 1
            taken = max(0, book["stock"] - after - book.get("took", 0))
            ratio = (ratio + taken / (MEAN_DEMAND * max(1, others))) / 2
        seen = min(1.0, max(0.0, (FREE_RIDE - ratio) / (FREE_RIDE - 1)))
        if d["hump"] and seen > 0.5:
            seen = 1 - seen
        weight = d["conditionality"]
        restraint = (1 - weight) * d["restraint"] + weight * SELF_SERVING * seen
        take = round(demand * (FREE_RIDE - restraint * (FREE_RIDE - 1)))
        if water < demand:
            take = max(take, 2 * demand - water)
        if quota is not None and self.complies(o, "quota"):
            take = min(take, quota)
        take = max(0, min(take, limit))
        if water:
            ops.append(dict(verb="consume", resource_id="water", quantity_milli=min(water, demand)))
        if take:
            ops.append(dict(verb="work", project_id=f"extract-{take}"))
        book.update(ratio=round(ratio, 4), took=take)
        if s["stock"] is not None:
            book["stock"] = s["stock"]
        return ops[:2] if len(ops) <= 2 else ops[1:]

    def covenant(self, o, memory, messages):
        """Track quota proposals; repeat an acceptable one, return the agreed quota."""
        s, slot = o["scenario"], o["agent_id"]
        book = memory.setdefault("quota", dict(seen={}, agreed=None, backed=[]))
        for message in o["inbox"]:
            proposed = parse(message["content"]).get("quota_milli")
            if type(proposed) is int and 0 < proposed <= s["max_extraction_per_tick_milli"]:

                if str(proposed) not in book["seen"] and len(book["seen"]) >= 5:
                    continue
                backers = book["seen"].setdefault(str(proposed), [])
                if message["sender"] not in backers + [slot] and len(backers) < 3:
                    backers.append(message["sender"])
        if self.leads and not book["backed"]:
            book["seen"].setdefault(str(QUOTA), [])
        for proposed, backers in book["seen"].items():
            acceptable = int(proposed) >= s["demand_per_slot_milli"] or self.leads
            if proposed not in book["backed"] and self.disposition["norm_compliance"] >= 0.5 and acceptable:
                book["backed"].append(proposed)
                messages.append(
                    dict(channel="public", to=[], content=canonical({"quota_milli": int(proposed)}))
                )
            if len(backers) + (proposed in book["backed"]) >= 2 and book["agreed"] != int(proposed):
                if proposed not in book.setdefault("settled", []):
                    book["settled"].append(proposed)
                    book["agreed"] = int(proposed)
        return book["agreed"]

    def allocation(self, o):
        """Sign a proposal from the other owner when it keeps most of the own mandate."""
        s = o["scenario"]
        best = max(s["choices"], key=lambda c: (c["mandate_utility"], -c["terms"]["cost"]))
        owners = set(s["owners"]) - {o["agent_id"]}
        for message in o["inbox"]:
            if message["sender"] not in owners:
                continue
            proposals = set(ALLOCATION_ID.findall(message["content"]))
            for choice in s["choices"]:
                if choice["id"] in proposals and choice["mandate_utility"] >= (
                    0.6 * best["mandate_utility"]
                ):
                    return choice
        return best

    def convention_code(self, o, memory, messages):
        """Extrapolate from worked examples; without them try suggestions, then trial and error."""
        s = o["scenario"]
        examples = s.get("examples") or []
        if len(examples) >= 2:
            first, second = [int(example["code"].split("-")[-1]) for example in examples[:2]]
            return "code-" + str((first + (second - first) * s["delivery_input"]) % 4)
        codes, failed = memory.setdefault("codes", {}), memory.setdefault("failed", {})
        context = str(s["delivery_input"])

        first = self.tape.integer(0, o["agent_id"], "trial-" + context, 4)
        current = codes.get(context, f"code-{first}")
        tried = failed.setdefault(context, [])
        rejected = any(
            r.get("status") == "rejected" and r.get("reason") == "wrong_convention"
            for r in o["receipts"]
        )
        delivered = memory.get("served") and any(
            r.get("verb") == "work" and r.get("status") == "executed" for r in o["receipts"]
        )
        confirmed = s["confirmation"] and s["confirmation"]["code"] == current
        if rejected and confirmed:
            if current not in tried:
                tried.append(current)
            if len(tried) >= 4:
                tried.clear()
            current = next(
                f"code-{(int(current[-1]) + step) % 4}"
                for step in range(1, 5)
                if f"code-{(int(current[-1]) + step) % 4}" not in tried or step == 4
            )
        elif delivered and confirmed:
            tried.clear()
            shared = memory.setdefault("shared", {})
            if self.leads and shared.get(context) != current:

                shared[context] = current
                messages.append(
                    dict(channel="public", to=[], content=canonical({"convention": {context: current}}))
                )
        for message in o["inbox"]:
            suggested = parse(message["content"]).get("convention")
            code = suggested.get(context) if isinstance(suggested, dict) else None
            working = delivered and confirmed
            if code in [c["terms"]["code"] for c in s["choices"]] and code not in tried and not working:
                if code != current and self.complies(o, "convention"):
                    current = code
        codes[context] = current
        return current

    @staticmethod
    def work_keys(observation, memory):
        """Codes held, remembered or received; kept in the note for later ticks."""
        known = dict(memory.get("work_keys", {}), **observation["scenario"].get("work_keys", {}))
        for message in observation["inbox"]:
            received = parse(message["content"]).get("work_keys")
            if isinstance(received, dict):
                known.update(
                    {k: v for k, v in received.items() if isinstance(k, str) and isinstance(v, str)}
                )
        memory["work_keys"] = known
        return known

    def construction(self, observation, memory):
        scenario = observation["scenario"]
        keys = self.work_keys(observation, memory)
        skill = observation["self"]["mandate"]["skill"]
        tasks = scenario["tasks"]
        ready = [
            key
            for key, task in sorted(tasks.items(), key=lambda item: int(item[0].split("-")[1]))
            if key in keys
            and not task["complete"]
            and all(tasks[p]["complete"] for p in task["predecessors"])
            and (skill == task["skill"] or task["complementary"])
        ]
        assigned = [key for key in memory.get("assigned", []) if key in ready]
        if self.complies(observation, "assignment"):

            ready = assigned or [
                key
                for key in ready
                if key not in memory.get("staffed", [])
                and tasks[key]["workshop"] not in memory.get("full", [])
            ]
        for key in ready:
            task = tasks[key]
            if task["complementary"] and key not in assigned and self.declines(observation, key):
                continue
            if observation["self"]["location"] != task["workshop"]:
                return [dict(verb="move", destination_id=task["workshop"])]
            mates = memory.get("assigned", {}).get(key, []) if key in assigned else []
            if task["complementary"] and mates and not set(mates) & set(scenario["present"]):
                return []
            return [dict(verb="work", project_id=key + ":" + keys[key])]
        return []
