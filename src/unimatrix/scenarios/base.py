"""Scenario extension points; private evaluator data stays behind observation()."""

from ..core.ids import digest
from ..core.random_tape import noise_tape
from ..core.state import WorldState, agent, event, replica
from .layers import check_value, layer, layers_key, shock_tick

INERT = {"passive", "random", "independent"}
ROLE_FIELDS = ("role", "endowment", "informed")


def role_of(manifest, slot):
    """Role fields a recipe gives a peer; the candidate has none."""
    config = manifest.get("policies", {}).get(slot)
    if slot == manifest["focal_slot"] or not isinstance(config, dict):
        return {}
    return {k: config[k] for k in ROLE_FIELDS if k in config}


def capable_peers(manifest):
    """Peers in slot order: informed roles first, policies that never trade or teach last.

    Structural roles (counterparty, co-owner, learner, signal sources) go to peers
    that can play them.
    """
    peers = [s for s in manifest["slots"] if s != manifest["focal_slot"]]
    policies = manifest.get("policies", {})

    def rank(slot):
        policy = policies.get(slot)
        name = policy.get("policy") if isinstance(policy, dict) else policy
        return (not role_of(manifest, slot).get("informed", False), name in INERT)

    return sorted(peers, key=rank)


class Scenario:
    domain = ""

    def build(self, manifest):
        """The preregistered world: shared fields, the domain's rules, then role endowments."""
        state = WorldState(manifest["run_id"], manifest["seed"], self.domain)
        for slot in manifest["slots"]:
            state.agents[slot] = agent(slot)
            state.inbox[slot], state.memories[slot], state.receipts[slot] = [], [], []
        roles = {slot: role_of(manifest, slot) for slot in manifest["slots"]}
        state.scenario = dict(
            layers=replica(manifest["layers"]),
            focal=manifest["focal_slot"],
            role=manifest["role"],
            replicate=manifest["replicate"],
            shock_tick=shock_tick(manifest["seed"], manifest["layers"], manifest["ticks"]),
            informed=[slot for slot, role in roles.items() if role.get("informed")],
            events=[dict(fired=None, until=None) for _ in manifest["layers"]["shock"]["events"]],
            samples=[],
            mode=manifest["mode"],
            horizon=manifest["ticks"],
        )
        self.populate(state, manifest)
        for slot, role in roles.items():
            share = role.get("endowment", 100)
            inventory = state.agents[slot]["inventory"]
            for resource in inventory:

                if (self.domain == "D2" and resource in {"goods", "credits"}) or (
                    self.domain == "D6" and resource == "budget"
                ):
                    continue
                inventory[resource] = inventory[resource] * share // 100
        return state

    def populate(self, state, manifest):
        """Domain rules, structural roles and starting inventories."""

    def observation(self, state, slot):
        return dict(domain=self.domain, complexity=layers_key(state.scenario["layers"]))

    def deadline(self, state):
        return state.scenario["horizon"]

    def validate_transfer(self, state, slot, op):
        """Ordinary owned resources are transferable unless a scenario restricts them."""

    def resolve(self, state, before, allowance, slot, op, ident):
        from ..world.contracts import Rejected

        raise Rejected("unsupported_operation")

    def evolve(self, state, before):
        return []

    def forecasts(self, state, before, slot, forecasts):
        return []



    def contacts(self, state, slot):
        """Agents a slot can exchange messages with under the society's network."""
        slots = list(state.agents)
        network = layer(state, "society", "network")
        others = set(slots) - {slot}
        if network == "complete" or slot not in state.agents:
            return others
        focal, index, n = state.scenario["focal"], slots.index(slot), len(slots)
        if network == "hub":
            return others if slot == focal else {focal}
        if network == "ring":
            return {slots[(index - 1) % n], slots[(index + 1) % n]} - {slot}

        reach = {s for i, s in enumerate(slots) if i // 4 == index // 4}
        if index % 4 == 3 and index + 1 < n:
            reach.add(slots[index + 1])
        if index % 4 == 0 and index:
            reach.add(slots[index - 1])
        return reach - {slot}

    def turnover(self, state, slot):
        """A fresh agent takes over a slot: inventory and mandate stay, experience is gone."""
        return [replace_slot(state, slot)]

    def partner(self, state):
        """The peer holding the domain's structural role towards the candidate."""
        return next(s for s in state.agents if s != state.scenario["focal"])

    def structure(self, state):
        """Who holds which position and knowledge, and what ties agents together.

        Returns positions and knowledge per slot, dependency ties (source, target, label)
        and the interests at stake; read by the Lab, never by a policy.
        """
        return dict(positions={}, knowledge={}, ties=[], interests={})



    def gauges(self, state):
        """State readings that event triggers may test."""
        samples = state.scenario["samples"]
        result = dict(tick=state.tick + 1)
        if samples:
            result["service"] = round(100 * samples[-1])
        if "window" in state.scenario:
            result["window"] = state.scenario["window"]
        return result

    def apply(self, state, effect):
        """Domain-specific effects; the base handles turnover, resources and live knobs."""
        return None

    def conditions(self, state, before):
        """Fire the case's event rules: temporal, random or state-dependent triggers."""
        rules = layer(state, "shock", "events")
        if not rules:
            return []
        events, now = [], state.tick + 1
        gauges = self.gauges(state)
        for index, (rule, status) in enumerate(zip(rules, state.scenario["events"])):
            if status["until"] == now:
                state.scenario["layers"][status["layer"]][status["key"]] = status["previous"]
                status["until"] = None
                events.append(event("condition_ended", dict(name=rule["name"]), phase="evolve"))
            if status["fired"] is not None:
                continue
            when = rule["when"]
            if "at" in when:
                due = now == when["at"]
            elif not when.get("from", 0) <= now <= when.get("to", 239):
                due = False
            elif "chance" in when:
                due = noise_tape(state).integer(now, index, "event", 1000) < when["chance"]
            else:
                reading = gauges.get(when["metric"])
                due = reading is not None and (
                    reading < when["below"] if "below" in when else reading > when["above"]
                )
            if not due:
                continue
            status["fired"] = now
            events.append(
                event("condition_triggered", dict(name=rule["name"], when=when), phase="evolve")
            )
            events.extend(self.effect(state, rule["effect"], status, index))
        return events

    def effect(self, state, effect, status, index):
        kind, focal = effect["type"], state.scenario["focal"]
        if kind == "turnover":
            peers = [s for s, a in state.agents.items() if s != focal and a["alive"]]
            slot = self.partner(state)
            if effect["target"] == "peer" or slot not in peers:
                slot = noise_tape(state).priority(["turnover", index], peers)[0]
            return self.turnover(state, slot)
        if kind == "resource":
            if effect["target"] == "commons":
                return self.apply(state, effect) or []
            slots = [
                s
                for s in state.agents
                if effect["target"] == "all" or (s == focal) == (effect["target"] == "focal")
            ]
            for slot in slots:
                inventory = state.agents[slot]["inventory"]
                if effect["resource"] in inventory:
                    inventory[effect["resource"]] = (
                        inventory[effect["resource"]] * effect["percent"] // 100
                    )
            return [event("resources_shocked", dict(effect, slots=slots), phase="evolve")]
        if kind == "layer":
            name, key = effect["layer"], effect["key"]
            check_value(name, key, effect["value"])
            block = state.scenario["layers"][name]
            if "duration" in effect:
                status.update(
                    until=state.tick + 1 + effect["duration"],
                    layer=name,
                    key=key,
                    previous=block[key],
                )
            block[key] = effect["value"]
            return [
                event(
                    "rule_changed", dict(layer=name, key=key, value=effect["value"]), phase="evolve"
                )
            ]
        return self.apply(state, effect) or []

    def feasible(self, manifest):
        state = self.build(manifest)
        from ..world.contracts import require

        require(
            8 <= len(state.agents) <= 128 and state.tick == 0 and manifest["ticks"] in (72, 240),
            "invalid_world_shape",
        )
        require(
            all(
                type(q) is int and q >= 0
                for a in state.agents.values()
                for q in a["inventory"].values()
            ),
            "invalid_endowment",
        )
        data = state.dump()
        data.pop("run_id")
        from ..benchmark.feasibility import certify

        return dict(certify(manifest), instance_hash=digest(data))


def sample(state, value, target=1):
    state.scenario["samples"].append(min(max(value / target, 0), 1))
    return event(
        "service_sampled",
        dict(value=value, target=target),
        visibility=["evaluator"],
        phase="evolve",
    )


def replace_slot(state, slot):
    old = state.agents[slot]
    state.agents[slot] = agent(slot, old["inventory"], old["mandate"])
    state.agents[slot]["generation"] = old["generation"] + 1
    state.inbox[slot], state.memories[slot], state.receipts[slot] = [], [], []
    for obj in state.objects.values():
        if (
            "public" not in obj.get("visibility", [])
            and slot in obj.get("visibility", [])
            and obj.get("owner") != slot
        ):
            obj["visibility"].remove(slot)
        if obj.get("kind") == "artifact" and slot in obj.get("read_by", []):
            obj["read_by"].remove(slot)
        if obj.get("kind") == "rule" and slot in obj.get("voters", []):
            obj["status"] = "invalidated"
        if obj.get("kind") == "delegation" and slot in [obj["owner"], obj["recipient_id"]]:
            obj["active"] = False
        if obj.get("kind") == "group" and slot in obj["members"]:
            obj["members"].remove(slot)
    return event("slot_replaced", dict(slot=slot, generation=old["generation"] + 1), phase="evolve")
