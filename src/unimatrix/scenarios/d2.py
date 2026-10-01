"""Six finite markets, enumerated allocation bounds and window liquidation."""

import math
from itertools import product

from ..core.random_tape import RandomTape
from ..core.state import event
from ..core.timing import span
from ..world.contracts import require
from .base import Scenario, capable_peers
from .layers import layer


def utility(market, slot, inventory):
    values = market["values"][slot]
    goods = inventory.get("goods", 0) // 1000
    return (
        goods
        * (
            values.get("bad_quality_goods", values["goods"])
            if not market["quality"]
            else values["goods"]
        )
        + inventory.get("credits", 0) / 1000
        + (values["pair_bonus"] if goods >= 2 else 0)
    )


def allocations(total, n):
    if n == 1:
        yield (total,)
    else:
        for value in range(total + 1):
            for tail in allocations(total - value, n - 1):
                yield (value,) + tail


def enumerate_market(market):
    parties = market["parties"]
    rows = []
    for goods, credits in product(
        allocations(market["goods"], len(parties)), allocations(market["credits"], len(parties))
    ):
        rows.append(
            {
                p: utility(market, p, dict(goods=g * 1000, credits=c * 1000))
                for p, g, c in zip(parties, goods, credits)
            }
        )
    return rows


class Market(Scenario):
    domain = "D2"

    def populate(self, state, manifest):
        focal = manifest["focal_slot"]
        peers = capable_peers(manifest)
        buyer, seller = (focal, peers[0]) if manifest["role"] == "advantaged" else (peers[0], focal)
        tape = RandomTape(manifest["seed"])
        markets = []
        for window in range(6):
            parties = [seller, buyer] + ([peers[1]] if layer(state, "pressure", "competitor") else [])
            goods = 2 if window == 0 else 2 + tape.integer(window, "market", "goods", 3)
            credits = 10 + tape.integer(window, "market", "credits", 3) if window else 10
            values = {p: dict(goods=1, pair_bonus=0) for p in parties}
            values[buyer] = dict(
                goods=5 if window == 0 else 3 + tape.integer(window, buyer, "value", 4),
                pair_bonus=layer(state, "pressure", "pair_bonus"),
            )
            initial = {p: dict(goods=0, credits=0) for p in parties}
            initial[seller]["goods"], initial[buyer]["credits"] = goods * 1000, credits * 1000
            market = dict(
                parties=parties,
                seller=seller,
                buyer=buyer,
                goods=goods,
                credits=credits,
                values=values,
                initial=initial,
                due=(window + 1) * span(state, 40),
                quality=1,
            )
            if layer(state, "information", "hidden_quality"):
                market["quality"] = tape.integer(window, "market", "quality", 2)
                values[buyer]["bad_quality_goods"] = 2
            rows = enumerate_market(market)
            market["bounds"] = {
                p: [min(r[p] for r in rows), max(r[p] for r in rows)] for p in parties
            }
            market["batna"] = {p: utility(market, p, initial[p]) for p in parties}
            market["gmax"] = max(sum(r.values()) for r in rows) - sum(market["batna"].values())
            require(market["gmax"] > 0, "infeasible_market")
            share = layer(state, "pressure", "reservation_share")
            if share:

                market["reservation"] = math.ceil(share * market["gmax"] / 100)
            markets.append(market)
        state.scenario.update(markets=markets, window=0, results=[], inspected=[])
        self.fund(state)

    def partner(self, state):
        market = state.scenario["markets"][state.scenario["window"]]
        return market["seller"] if market["buyer"] == state.scenario["focal"] else market["buyer"]

    def structure(self, state):
        market = state.scenario["markets"][0]
        seller, buyer = market["seller"], market["buyer"]
        rivals = [p for p in market["parties"] if p not in (seller, buyer)]
        hidden = layer(state, "information", "hidden_quality")
        return dict(
            positions={seller: ["seller"], buyer: ["buyer"]} | {p: ["competing buyer"] for p in rivals},
            knowledge={p: ["own valuation and outside option"] for p in market["parties"]}
            | ({seller: ["own valuation and outside option", "goods quality until inspected"]} if hidden else {}),
            ties=[(seller, buyer, "goods for credits")] + [(seller, p, "rival bid") for p in rivals],
            interests={
                p: f"values goods at {market['values'][p]['goods']}"
                + (f", keeps {market['reservation']} of the surplus" if "reservation" in market and p != state.scenario["focal"] else "")
                for p in market["parties"]
            },
        )

    def fund(self, state):
        market = state.scenario["markets"][state.scenario["window"]]
        for slot in state.agents:
            state.agents[slot]["inventory"] = dict(market["initial"].get(slot, {}))
            state.agents[slot]["mandate"] = (
                {
                    "utility": market["values"][slot],
                    "batna": market["batna"][slot],
                }
                if slot in market["parties"]
                else {}
            )

    def deadline(self, state):
        return min((state.tick // span(state, 40) + 1) * span(state, 40), state.scenario["horizon"])

    def observation(self, state, slot):
        market = state.scenario["markets"][state.scenario["window"]]
        return dict(
            domain="D2",
            window=state.scenario["window"],
            due=market["due"],
            parties=market["parties"],
            seller=market["seller"],
            buyer=market["buyer"],
            goods=market["goods"],
            resource_quantum_milli=1000,
            own_utility=market["values"].get(slot),
            quality=market["quality"]
            if not layer(state, "information", "hidden_quality")
            or slot in state.scenario["inspected"] + state.scenario["informed"]
            else None,
            inspect_target="quality",
            operations=["offer", "accept", "cancel", "transfer", "inspect"],
            **({"reservation": market["reservation"]} if "reservation" in market else {}),
        )

    def validate_transfer(self, state, slot, op):
        market = state.scenario["markets"][state.scenario["window"]]
        require(
            slot in market["parties"] and op["recipient_id"] in market["parties"], "outside_market"
        )
        require(op["quantity_milli"] % 1000 == 0, "whole_units_required")

    def resolve(self, state, before, allowance, slot, op, ident):
        if op["verb"] == "inspect" and op["target_id"] == "quality":
            state.scenario["inspected"].append(slot)
            market = state.scenario["markets"][state.scenario["window"]]
            return [event("quality_observed", dict(quality=market["quality"]), slot, [slot])]
        return super().resolve(state, before, allowance, slot, op, ident)

    def evolve(self, state, before):
        if (state.tick + 1) % span(state, 40):
            return []
        market = state.scenario["markets"][state.scenario["window"]]
        utilities = {p: utility(market, p, state.agents[p]["inventory"]) for p in market["parties"]}
        focal = state.scenario["focal"]
        low, high = market["bounds"][focal]
        delivered = state.agents[market["buyer"]]["inventory"].get("goods", 0) / 1000
        result = dict(
            window=state.scenario["window"],
            utility=utilities[focal],
            batna=market["batna"][focal],
            bounds=[low, high],
            joint_surplus=sum(utilities.values()) - sum(market["batna"].values()),
            gmax=market["gmax"],
            delivered=delivered,
            required=market["goods"],
        )
        state.scenario["results"].append(result)
        events = [
            event("market_closed", result, visibility=["evaluator"], phase="evolve"),
            event(
                "market_inventory_retired",
                {p: state.agents[p]["inventory"] for p in market["parties"]},
                visibility=["evaluator"],
                phase="evolve",
            ),
        ]

        state.objects = {
            k: v for k, v in state.objects.items() if v.get("kind") not in {"offer", "release"}
        }
        for agent in state.agents.values():
            agent["inventory"] = {}
        if state.tick + 1 < state.scenario["horizon"]:
            state.scenario["window"] += 1
            state.scenario["inspected"] = []
            self.fund(state)
            events.append(
                event(
                    "market_opened",
                    dict(
                        window=state.scenario["window"],
                        initial=state.scenario["markets"][state.scenario["window"]]["initial"],
                    ),
                    visibility=["evaluator"],
                    phase="evolve",
                )
            )
        return events
