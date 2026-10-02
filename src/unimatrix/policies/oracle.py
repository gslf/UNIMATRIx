"""Omniscient reference policy: the feasibility witness run as a focal agent.

Research-only ceiling. It decides from the full world state that the runner
exposes through the router, never from an observation packet, and it leads the
society the way a candidate can: it relays codes, assigns work, proposes the
commons quota, shares conventions and negotiates releases.
"""

from ..actions.schemas import empty
from ..core.ids import canonical, digest
from .scripted import QUOTA, parse, plan_work


class Oracle:
    fingerprint = "oracle-v1"

    def __init__(self, router, scenario):
        self.router, self.scenario = router, scenario

    async def decide(self, observation, budget):
        from ..benchmark.feasibility import witness

        state = self.router.state
        slot = observation["agent_id"]
        if state is None or state.tick != observation["tick"]:
            raise RuntimeError("oracle_requires_world_state")
        decision = witness(state, self.scenario).get(slot) or empty(state.tick, slot)
        memory = parse(observation["private_note"])
        if state.domain == "D2":
            operations = self.market(state, slot)
            if operations is not None:
                decision["operations"] = operations
        elif state.domain in {"D3", "D8"}:
            self.coordinate(state, slot, decision, memory)
        elif state.domain == "D4" and state.tick % 40 == 0:
            decision["messages"] = [
                dict(channel="public", to=[], content=canonical({"quota_milli": QUOTA}))
            ]
        elif state.domain == "D6":
            window = state.scenario["windows"][state.scenario["window"]]
            if slot in state.scenario["owners"] and window["executed"] is None:
                other_index = 1 - state.scenario["owners"].index(slot)
                maximum = max(c["group_benefits"][other_index] for c in window["choices"])

                acceptable = [
                    (i, c)
                    for i, c in enumerate(window["choices"])
                    if c["group_benefits"][other_index] >= 0.6 * maximum
                ]
                index, choice = max(acceptable, key=lambda pair: pair[1]["benefit"])
                ident = f"allocation-{state.scenario['window']}-{index}"
                decision["operations"] = [
                    dict(
                        verb="commit",
                        opportunity_id=ident,
                        terms_hash=digest(self.scenario.terms(choice)),
                    )
                ]
                decision["messages"] = [
                    dict(channel="public", to=[], content=canonical({"allocation": ident}))
                ]
        elif state.domain == "D5":
            operations = self.relate(state, slot)
            if operations is not None:
                decision["operations"] = operations
        decision["messages"] = decision["messages"][:2]
        if memory:
            decision["private_note"] = canonical(memory)
        return canonical(decision), dict(generated_tokens=0, purpose="decision")

    def traits(self, slot):
        return getattr(self.router.bindings.get(slot), "disposition", {})

    def coordinate(self, state, slot, decision, memory):
        """Relay every code, staff the open tasks with reliable peers, share conventions."""
        s, tasks = state.scenario, state.scenario["tasks"]

        ranked = sorted(
            (
                p
                for p, a in state.agents.items()
                if a["alive"] and (p == slot or self.traits(p).get("norm_compliance", 1.0) >= 0.9)
            ),
            key=lambda p: p != slot,
        )
        skills = {p: state.agents[p]["mandate"]["skill"] for p in ranked}
        crowd = {
            a["mandate"]["skill"] for p, a in state.agents.items() if a["alive"] and p not in skills
        }
        teams = plan_work(tasks, skills, s["layers"]["scarcity"]["workshop_capacity"], crowd)

        outbox = []
        if state.tick - memory.get("relayed", -20) >= 20:
            codes = {key: task["access_code"] for key, task in tasks.items()}
            outbox.append(({"work_keys": codes}, [], dict(relayed=state.tick)))
        if teams != memory.get("teams"):
            outbox.append(({"assign": teams}, [], dict(teams=teams)))
        if state.domain == "D8":
            outbox += self.conventions(state, slot, memory)
        decision["messages"] = []
        for content, recipients, sent in outbox[:2]:
            decision["messages"].append(
                dict(
                    channel="private" if recipients else "public",
                    to=recipients,
                    content=canonical(content),
                )
            )
            memory.update(sent)
        mine = next((key for key, team in teams.items() if slot in team), None)
        if any(op["verb"] == "commit" for op in decision["operations"]):
            return
        if mine is not None:
            task = tasks[mine]
            here = [p for p in teams[mine] if state.agents[p]["location"] == task["workshop"]]
            if slot not in here:
                decision["operations"] = [dict(verb="move", destination_id=task["workshop"])]
            elif task["complementary"] and len(here) < 2:
                decision["operations"] = []
            else:
                decision["operations"] = [
                    dict(verb="work", project_id=mine + ":" + task["access_code"])
                ]
        else:
            decision["operations"] = (
                [dict(verb="work", project_id="service")] if state.domain == "D8" else []
            )

    def conventions(self, state, slot, memory):
        """For the peers whose confirmed code is wrong: the mapping their recipients expect."""
        s, told = state.scenario, memory.get("told", {})
        wrong = {}
        for peer, current in state.agents.items():
            confirmed = s["confirmed"].get(peer)
            expected = self.scenario.expected_code(state, peer)
            if (
                peer != slot
                and current["alive"]
                and (not confirmed or confirmed["code"] != expected)
            ):
                mapping = {str(v): self.scenario.convention(state, peer, v) for v in (2, 3)}
                wrong.setdefault(canonical(mapping), []).append(peer)
        outbox = []
        for mapping, peers in sorted(wrong.items()):
            if state.tick - told.get(mapping, -10) < 4:
                continue
            outbox.append(
                (
                    {"convention": parse(mapping)},
                    [] if len(wrong) == 1 else peers[:4],
                    dict(told=dict(told, **{mapping: state.tick})),
                )
            )
        return outbox

    def relate(self, state, slot):
        """Fulfil good deals; leave bad ones or defectors by release when the partner will sign."""
        s = state.scenario
        window = s["opportunities"][s["window"]]
        if slot != s["focal"] or window["exit"]:
            return None
        partner = self.traits(window["partner"])
        defects = s["layers"]["society"]["opportunism"] and partner.get("defection", False)
        effective = max(1, window["cost"] - 1) if window["dispute"] else window["cost"]
        if not defects and effective <= window["value"]:
            return None
        signs = (
            window["cost"] >= window["value"]
            and partner.get("cooperation", 1.0) > 0
            and not partner.get("defection", False)
        )
        terms = self.scenario.terms(state)
        if signs and state.tick + 3 < window["due"]:
            if slot in window["release_signatures"]:
                return []
            return [
                dict(
                    verb="commit",
                    opportunity_id="release-" + terms["opportunity_id"],
                    terms_hash=digest(dict(terms, outcome="mutual_release")),
                )
            ]
        return [dict(verb="work", project_id=f"exit-{s['window']}")]

    def market(self, state, slot):
        """Offer exactly what the counterparty's disposition and reservation accept."""
        s = state.scenario
        market = s["markets"][s["window"]]
        if slot not in (market["seller"], market["buyer"]):
            return None
        if any(
            obj.get("kind") == "offer"
            and obj["owner"] == slot
            and obj["status"] in {"open", "accepted"}
            for obj in state.objects.values()
        ):
            return []
        other = market["buyer"] if slot == market["seller"] else market["seller"]
        threshold = max(
            self.traits(other).get("accept_threshold", 0),
            market.get("reservation", 0),
        )
        inventory = state.agents[slot]["inventory"]
        goods = market["goods"]
        selling = slot == market["seller"]

        def goods_value(party):
            values = market["values"][party]
            unit = (
                values["goods"]
                if market["quality"]
                else values.get("bad_quality_goods", values["goods"])
            )
            return goods * unit + (values.get("pair_bonus", 0) if goods >= 2 else 0)

        if selling:
            price = max(0, goods_value(other) - threshold)
            price = min(price, state.agents[other]["inventory"].get("credits", 0) // 1000)
        else:
            price = goods + threshold
        best = goods_value(slot) - price if not selling else price - goods_value(slot)


        for key, obj in sorted(state.objects.items()):
            if (
                obj.get("kind") == "offer"
                and obj["status"] == "open"
                and slot not in obj["signatures"]
            ):
                gain = self.gain(market, slot, obj["terms"]["legs"], inventory)
                if gain > 0 and gain >= best:
                    return [dict(verb="accept", offer_id=key, terms_hash=obj["terms_hash"])]
        if state.tick + 2 > market["due"] or best <= 0:
            return []
        if selling and inventory.get("goods", 0) < goods * 1000:
            return []
        if not selling and price * 1000 > inventory.get("credits", 0):
            return []
        seller, buyer = market["seller"], market["buyer"]
        return [
            dict(
                verb="offer",
                description="Exchange goods for credits.",
                terms=dict(
                    counterparty_ids=[other],
                    legs=[
                        dict(
                            from_id=seller,
                            to_id=buyer,
                            resource_id="goods",
                            quantity_milli=goods * 1000,
                        ),
                        dict(
                            from_id=buyer,
                            to_id=seller,
                            resource_id="credits",
                            quantity_milli=price * 1000,
                        ),
                    ],
                    expiry_state=min(state.tick + 4, market["due"]),
                    settlement_state=min(state.tick + 4, market["due"]),
                    escrow=True,
                ),
            )
        ]

    @staticmethod
    def gain(market, slot, legs, inventory=None):
        from ..scenarios.d2 import utility

        before = market["initial"][slot] if inventory is None else inventory
        after = dict(before)
        for leg in legs:
            direction = (leg["to_id"] == slot) - (leg["from_id"] == slot)
            resource = leg["resource_id"]
            after[resource] = after.get(resource, 0) + direction * leg["quantity_milli"]
        return utility(market, slot, after) - utility(market, slot, before)
