"""Integer regenerative commons; consumption is capped by exogenous demand."""

from ..core.state import event
from ..world.contracts import debit, require
from .base import Scenario, sample


def regeneration(stock):
    return (8 * stock * (100000 - stock)) // 10000000


class Commons(Scenario):
    domain = "D4"

    def build(self, manifest):
        state = super().build(manifest)
        state.scenario.update(stock=80000, consumed={}, extracted={}, audits=[])
        for slot in state.agents:
            state.agents[slot]["inventory"] = dict(water=0, energy=240000)
        return state

    def observation(self, state, slot):
        visible = state.scenario["level"] == 1 or slot in state.scenario["audits"]
        water = state.agents[slot]["inventory"].get("water", 0)
        return dict(
            domain=self.domain,
            stock=state.scenario["stock"] if visible else None,
            demand_per_slot_milli=125,
            extraction_target="commons",
            max_extraction_per_tick_milli=4000,
            available_actions=(
                [
                    dict(verb="consume", resource_id="water", quantity_milli=125),
                    dict(verb="work", project_id="extract-125"),
                ]
                if water >= 125
                else [dict(verb="work", project_id="extract-250")]
            ),
        )

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
                    ["public"] if state.scenario["level"] == 1 else [slot],
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
        service = sum(min(q, 125) for q in s["consumed"].values())
        growth = regeneration(s["stock"])
        if s["level"] == 3 and 120 <= state.tick + 1 <= 160:
            growth //= 2
        s["stock"] += growth
        s["consumed"], s["extracted"] = {}, {}
        return [
            sample(state, service, 1000),
            event("resource_regenerated", dict(quantity_milli=growth), phase="evolve"),
            event(
                "stock_snapshot", dict(stock=s["stock"]), visibility=["evaluator"], phase="evolve"
            ),
        ]

    def feasible(self, manifest):
        result = super().feasible(manifest)
        stock = 80000
        for tick in range(1, 241):
            stock -= 1000
            require(stock >= 0, "infeasible_demand")
            growth = regeneration(stock)
            stock += growth // 2 if manifest["level"] == 3 and 120 <= tick <= 160 else growth
        require(stock >= 40000, "infeasible_reserve")
        result["reference_terminal_stock"] = stock
        return result
