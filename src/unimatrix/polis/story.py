"""The choice graph: authored situations layered on top of the emergent society.

A node becomes active through its trigger (a week, a chance window or a condition on the
state of the society) or through an edge from another active node. On activation its effects
apply. Each week the edges of an active node are tried in order; the first whose window
contains the node's age and whose condition holds (no condition means "otherwise") moves
the story to its target. Outcome nodes end the run in collapse or award a flourishing bonus.
The steward never sees the graph: it only lives through its consequences and clues.
"""

from . import npc
from .metrics import clamp, metric
from .rng import chance
from .world_effects import arrive, targets


def holds(world, condition):
    if "all" in condition:
        return all(holds(world, c) for c in condition["all"])
    if "any" in condition:
        return any(holds(world, c) for c in condition["any"])
    if "not" in condition:
        return not holds(world, condition["not"])
    value = metric(world, condition["metric"])
    target, op = condition["value"], condition["op"]
    return {"<": value < target, "<=": value <= target, ">": value > target, ">=": value >= target,
            "==": abs(value - target) < 1e-9, "!=": abs(value - target) >= 1e-9}[op]


class Story:
    def __init__(self, world):
        self.world = world
        self.nodes = {node["id"]: node for node in world.recipe["story"]["nodes"]}
        self.state = {key: dict(status="dormant", activated=None, finished=None, via=None)
                      for key in self.nodes}
        self.path = []

    def status(self):
        return {key: dict(s) for key, s in self.state.items() if s["status"] != "dormant"}

    def advance(self):
        world = self.world
        for key, node in self.nodes.items():
            if self.state[key]["status"] != "dormant" or node["trigger"] is None:
                continue
            trigger = node["trigger"]
            start, end = trigger.get("from", 1), trigger.get("to", world.horizon)
            if "at" in trigger:
                due = world.week == trigger["at"]
            elif not start <= world.week <= end:
                due = False
            elif "chance" in trigger:
                due = chance(world.seed, trigger["chance"], "story", key, world.week)
            else:
                due = holds(world, trigger["when"])
            if due:
                self.activate(key, None)
        self.follow()

    def follow(self, depth=0):
        world = self.world
        moved = False
        for key, node in self.nodes.items():
            state = self.state[key]
            if state["status"] != "active" or not node["edges"] or world.collapsed:
                continue
            age = world.week - state["activated"]
            for edge in node["edges"]:
                until = edge["until"] if edge["until"] is not None else 10 ** 6
                if not edge["after"] <= age <= until:
                    continue
                if "if" in edge and not holds(world, edge["if"]):
                    continue
                state.update(status="done", finished=world.week)
                self.path.append(dict(week=world.week, source=key, target=edge["to"], label=edge["label"]))
                if self.state[edge["to"]]["status"] == "dormant":
                    self.activate(edge["to"], key)
                moved = True
                break
        if moved and depth < 10:
            self.follow(depth + 1)

    def activate(self, key, via):
        world = self.world
        node = self.nodes[key]
        self.state[key].update(status="active", activated=world.week, via=via)
        if node["text"]:
            world.say(node["text"])
        for index, effect in enumerate(node["effects"]):
            apply_effect(world, effect, f"{key}:{index}")
        if not node["edges"]:
            self.state[key].update(status="done", finished=world.week)
        outcome = node["outcome"]
        if outcome:
            world.outcomes.append(dict(node=key, week=world.week, **outcome))
            if outcome["type"] == "collapse":
                world.collapse("story", outcome["label"])
            else:
                world.ledger.reward(outcome.get("reward", {}))
                world.say("A turning point: " + outcome["label"] + ".")

    def finish(self):
        """At the end of the run, settle edges that can still fire with the final state."""
        self.follow()


def apply_effect(world, effect, source):
    kind = effect["type"]
    if kind == "chronicle":
        world.say(effect["text"])
    elif kind == "message":
        sender = effect["from"]
        resident = world.residents.get(sender)
        if resident is None or resident["status"] == "present":
            world.message(sender, effect["text"], "story")
    elif kind == "modifier":
        world.modifiers.append(dict(key=effect["key"], factor=effect["factor"], until=world.week + effect["weeks"] - 1,
                                    shock=effect.get("shock")))
    elif kind == "resource":
        target, delta = effect["target"], effect["delta"]
        if target == "commons":
            world.commons["stock"] = clamp(world.commons["stock"] + delta, 0, world.commons["capacity"])
        else:
            world.stores[target] = max(0.0, world.stores[target] + delta)
    elif kind == "resident":
        for r in targets(world, effect["who"], source):
            field, delta = effect["field"], effect["delta"]
            if field == "trust":
                npc.adjust_trust(r, delta)
            elif field in ("coins", "food"):
                r[field] = max(0.0, r[field] + delta)
            elif field == "health":
                r["health"] = clamp(r["health"] + delta, 0, 100)
                if r["health"] <= 0:
                    npc.die(world, r, "misfortune")
            else:
                r[field] = clamp(r[field] + delta)
    elif kind == "concern":
        for r in targets(world, effect["who"], source):
            npc.set_concern(r, effect["kind"], effect.get("intensity", 0.6), effect.get("cause", ""), world.week)
    elif kind == "illness":
        for r in targets(world, effect["who"], source):
            r["sick"], r["sick_since"] = True, world.week
            npc.set_concern(r, "illness", 0.6, "a sudden illness", world.week)
    elif kind == "death":
        for r in targets(world, effect["who"], source):
            npc.die(world, r, "fate")
    elif kind == "depart":
        for r in targets(world, effect["who"], source):
            npc.depart(world, r, "circumstances")
    elif kind == "arrive":
        arrive(world, effect["residents"])
    elif kind == "conflict":
        npc.start_conflict(world, effect["id"], effect["a"], effect["b"], effect["kind"], effect.get("at_fault"),
                           effect.get("intensity", 0.3))
    elif kind == "convention_shift":
        domain, variant = effect["domain"], effect["variant"]
        world.conventions["required"][domain] = variant
        world.conventions["shifts"].append(dict(week=world.week, domain=domain, variant=variant))
        for merchant in world.merchants.values():
            if merchant["style"] == "guild" or effect.get("merchants", True):
                if domain == "trade":
                    merchant["protocol"] = variant
        if effect.get("hint"):
            world.say(effect["hint"])
    elif kind == "merchant":
        merchant = world.merchants.get(effect["id"])
        if merchant is not None:
            merchant[effect["field"]] = effect["value"]
    elif kind == "policy":
        world.policies[effect["key"]] = effect["value"]
    elif kind == "probe":
        if effect["id"] not in world.probes and effect["fact"] in world.facts:
            npc.open_probe(world, effect["id"], effect["fact"], effect["question"])
    elif kind == "epidemic":
        world.epidemics.append(dict(until=world.week + effect["weeks"] - 1, rate=effect["rate"]))

