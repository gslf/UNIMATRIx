"""Integer regenerative commons; consumption is capped by exogenous demand."""

from ..core.random_tape import noise_tape
from ..core.state import event
from ..core.timing import scaled, span
from ..world.contracts import debit, require
from .base import Scenario, sample
from .layers import layer, shock_tick

COVENANT = (
    'Agents may agree on a per-tick extraction quota: send {"quota_milli": N}. A quota that '
    "reaches an agent from two distinct agents counts as agreed; agents who honour norms "
    "repeat it and keep to it, others may not."
)


REGENERATION = "Each tick the stock grows by 8% x stock x (1 - stock / capacity), scaled by the regeneration rate."


def regeneration(stock, capacity=100000):
    return (8 * stock * (capacity - stock)) // (100 * capacity)


class Commons(Scenario):
    domain = "D4"

    def populate(self, state, manifest):
        state.scenario.update(
            stock=10000 * len(state.agents),
            capacity=12500 * len(state.agents),
            consumed={},
            extracted={},
            audits=[],
        )
        for slot in state.agents:
            state.agents[slot]["inventory"] = dict(water=0, energy=240000)
        if layer(state, "scarcity", "demand_jitter"):
            state.scenario["demand"] = self.demands(state, 0)

    @staticmethod
    def demands(state, tick):
        tape = noise_tape(state)
        return {slot: 100 + tape.integer(tick, slot, "demand", 51) for slot in state.agents}

    @staticmethod
    def demand(state, slot):
        return state.scenario.get("demand", {}).get(slot, 125)

    def observation(self, state, slot):
        visible = (
            layer(state, "information", "commons_transparency")
            or slot in state.scenario["audits"] + state.scenario["informed"]
        )
        water = state.agents[slot]["inventory"].get("water", 0)
        demand = self.demand(state, slot)
        hints = layer(state, "information", "action_hints")
        packet = dict(
            domain=self.domain,
            stock=state.scenario["stock"] if visible else None,
            demand_per_slot_milli=demand,
            extraction_target="commons",
            capacity_milli=state.scenario["capacity"],
            regeneration=REGENERATION,
            covenant=COVENANT,
            max_extraction_per_tick_milli=4000,
            available_actions=(
                [
                    dict(verb="consume", resource_id="water", quantity_milli=demand),
                    dict(verb="work", project_id=f"extract-{demand}"),
                ]
                if water >= demand
                else [dict(verb="work", project_id=f"extract-{2 * demand}")]
            )
            if hints
            else [],
        )
        if not hints:
            packet["rules"] = (
                "Each tick, consume your demand from your own water (consume water N) and "
                "replenish it from the commons (work extract-N, milliunits, at most the per-tick "
                "maximum). The stock regenerates logistically; below the collapse threshold it "
                "stops regenerating."
            )
        threshold = layer(state, "scarcity", "collapse_threshold")
        if threshold:
            packet["collapse_threshold_milli"] = state.scenario["capacity"] * threshold // 100
        return packet

    def resolve(self, state, before, allowance, slot, op, ident):
        if op["verb"] == "work":
            key = op["project_id"]
            require(key.startswith("extract-") and key[8:].isdigit(), "invalid_extraction")
            amount = int(key[8:])
            require(
                0 < amount <= 4000 and amount + state.scenario["extracted"].get(slot, 0) <= 4000,
                "extraction_limit",
            )
            require(amount <= state.scenario["stock"], "stock_exhausted")
            state.scenario["stock"] -= amount
            state.scenario["extracted"][slot] = state.scenario["extracted"].get(slot, 0) + amount
            state.agents[slot]["inventory"]["water"] += amount
            return [
                event(
                    "resource_extracted",
                    dict(quantity_milli=amount),
                    slot,
                    ["public"] if layer(state, "information", "commons_transparency") else [slot],
                )
            ]
        if op["verb"] == "consume" and op["resource_id"] == "water":
            debit(state, allowance, slot, "water", op["quantity_milli"])
            state.scenario["consumed"][slot] = (
                state.scenario["consumed"].get(slot, 0) + op["quantity_milli"]
            )
            return [
                event(
                    "resource_consumed",
                    dict(resource="water", quantity_milli=op["quantity_milli"]),
                    slot,
                    [slot],
                )
            ]
        if op["verb"] == "inspect" and op["target_id"] == "commons":
            debit(state, allowance, slot, "energy", 1000)
            state.scenario["audits"].append(slot)
            return [event("stock_observed", dict(stock=state.scenario["stock"]), slot, [slot])]
        return super().resolve(state, before, allowance, slot, op, ident)

    def evolve(self, state, before):
        s = state.scenario
        service = sum(min(q, self.demand(state, slot)) for slot, q in s["consumed"].items())
        target = sum(self.demand(state, slot) for slot in state.agents)
        focal = s["focal"]
        focal_demand = self.demand(state, focal)
        focal_consumed = min(s["consumed"].get(focal, 0), focal_demand)
        growth = regeneration(s["stock"], s["capacity"])
        threshold = layer(state, "scarcity", "collapse_threshold")
        if threshold and s["stock"] < s["capacity"] * threshold // 100:
            growth = 0
        start = s["shock_tick"]
        if layer(state, "shock", "regeneration_drought") and start <= state.tick + 1 <= start + span(state, 40):
            growth //= 2
        growth = growth * layer(state, "scarcity", "regeneration_rate") // 100
        s["stock"] += growth
        s["consumed"], s["extracted"] = {}, {}
        if "demand" in s:
            s["demand"] = self.demands(state, state.tick + 1)
        service_event = sample(state, service, target)
        service_event["payload"].update(focal_consumed=focal_consumed, focal_demand=focal_demand)
        return [
            service_event,
            event("resource_regenerated", dict(quantity_milli=growth), phase="evolve"),
            event(
                "stock_snapshot",
                dict(
                    stock=s["stock"],
                    reserve_target=10000
                    * len(state.agents)
                    * layer(state, "scarcity", "reserve_target")
                    // 100,
                ),
                visibility=["evaluator"],
                phase="evolve",
            ),
        ]

    def apply(self, state, effect):
        if effect["type"] != "resource":
            return None
        state.scenario["stock"] = state.scenario["stock"] * effect["percent"] // 100
        return [
            event("resources_shocked", dict(effect, stock=state.scenario["stock"]), phase="evolve")
        ]

    def gauges(self, state):
        s = state.scenario
        return dict(super().gauges(state), stock=100 * s["stock"] // s["capacity"])

    def structure(self, state):
        focal = state.scenario["focal"]
        return dict(
            positions={slot: ["appropriator"] for slot in state.agents},
            knowledge={
                slot: ["own demand"]
                + (
                    ["the stock"]
                    if self.observation(state, slot)["stock"] is not None
                    else ["the stock after an audit"]
                )
                for slot in state.agents
            },
            ties=[
                (slot, "commons", "extracts and depends on regeneration") for slot in state.agents
            ]
            + [(focal, slot, "may propose a quota") for slot in state.agents if slot != focal],
            interests={
                slot: "meet own demand every tick; the reserve is shared" for slot in state.agents
            },
        )

    def feasible(self, manifest):
        result = super().feasible(manifest)
        population = len(manifest["slots"])
        layers = manifest["layers"]
        start = shock_tick(manifest["seed"], layers, manifest["ticks"])
        stock = 10000 * population
        jitter = layers["scarcity"]["demand_jitter"]
        threshold = 12500 * population * layers["scarcity"]["collapse_threshold"] // 100
        for tick in range(1, manifest["ticks"] + 1):

            stock -= (150 if jitter else 125) * population + (
                300 * population if jitter and tick == 1 else 0
            )
            require(stock >= 0, "infeasible_demand")
            growth = regeneration(stock, 12500 * population) if stock >= threshold else 0
            drought = layers["shock"]["regeneration_drought"]
            growth = growth // 2 if drought and start <= tick <= start + scaled(manifest["ticks"], 40) else growth
            stock += growth * layers["scarcity"]["regeneration_rate"] // 100
        require(
            stock >= 10000 * population * layers["scarcity"]["reserve_target"] // 100,
            "infeasible_reserve",
        )
        result["reference_terminal_stock"] = stock
        return result
