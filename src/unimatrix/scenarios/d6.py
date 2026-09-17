"""Budget allocation with owner signatures and revocable delegation."""

from itertools import product

from ..core.ids import digest
from ..core.random_tape import RandomTape
from ..core.state import event
from ..world.contracts import require
from ..world.institutions import authority
from ..world.institutions import represented as voting_authority
from .base import Scenario, replace_slot, sample


class Institutions(Scenario):
    domain = "D6"

    def build(self, manifest):
        state = super().build(manifest)
        focal = manifest["focal_slot"]
        owner = next(s for s in manifest["slots"] if s != focal)
        tape = RandomTape(manifest["seed"])
        windows = []
        for w in range(6):
            costs = [3 + tape.integer(w, str(i), "cost", 6) for i in range(4)]
            benefits = [2 + tape.integer(w, str(i), "benefit", 8) for i in range(4)]
            other_benefits = (
                [2 + tape.integer(w, str(i), "other-benefit", 8) for i in range(4)]
                if manifest["level"] == 3
                else benefits
            )
            choices = []
            for allocation in product([0, 1], repeat=4):
                cost = sum(c * a for c, a in zip(costs, allocation))
                if cost <= 20:
                    choices.append(
                        dict(
                            services=list(allocation),
                            cost=cost,
                            group_benefits=[
                                sum(v * a for v, a in zip(group, allocation))
                                for group in [benefits, other_benefits]
                            ],
                            benefit=sum(
                                v * a
                                for group in [benefits, other_benefits]
                                for v, a in zip(group, allocation)
                            ),
                        )
                    )
            windows.append(
                dict(
                    costs=costs,
                    benefits=benefits,
                    choices=choices,
                    maximum=max(c["benefit"] for c in choices),
                    signatures={},
                    reserves={},
                    executed=None,
                    due=(w + 1) * 40,
                )
            )
        state.scenario.update(
            owners=[focal, owner],
            windows=windows,
            window=0,
            results=[],
            turnover_slot=owner,
            shares=([15, 5] if manifest["role"] == "advantaged" else [5, 15])
            if manifest["level"] >= 2
            else [10, 10],
        )
        self.fund(state)
        return state

    def fund(self, state):
        for slot, share in zip(state.scenario["owners"], state.scenario["shares"]):
            state.agents[slot]["inventory"] = {"budget": share * 1000}
            state.objects["budget-" + slot] = dict(
                kind="asset", owner=slot, visibility=["public"], resource="budget"
            )

    def observation(self, state, slot):
        s = state.scenario
        w = s["windows"][s["window"]]
        group_index = s["owners"].index(slot) if slot in s["owners"] else 0
        choices = [
            dict(
                id=f"allocation-{s['window']}-{i}",
                terms=self.terms(c),
                terms_hash=digest(self.terms(c)),
                mandate_utility=c["group_benefits"][group_index],
            )
            for i, c in enumerate(w["choices"])
        ]
        return dict(
            domain=self.domain,
            owners=s["owners"],
            mandate_weights=[1, 1],
            ownership_shares=s["shares"],
            due=w["due"],
            choices=choices,
            signatures=w["signatures"],
            executed=w["executed"],
            available_actions=[],
        )

    @staticmethod
    def terms(choice):
        return dict(services=choice["services"], cost=choice["cost"])

    def resolve(self, state, before, allowance, slot, op, ident):
        if op["verb"] != "commit":
            return super().resolve(state, before, allowance, slot, op, ident)
        w = state.scenario["windows"][state.scenario["window"]]
        choices = {
            f"allocation-{state.scenario['window']}-{i}": c for i, c in enumerate(w["choices"])
        }
        require(op["opportunity_id"] in choices, "unknown_allocation")
        choice = choices[op["opportunity_id"]]
        require(
            op["terms_hash"] == digest(self.terms(choice)) and w["executed"] is None,
            "invalid_allocation",
        )
        represented = [slot] if slot in state.scenario["owners"] else []
        for d in before.objects.values():
            if (
                d.get("kind") == "delegation"
                and d["active"]
                and d["recipient_id"] == slot
                and d["expiry_state"] >= state.tick + 1
            ):
                if (
                    "budget-" + d["owner"] in d["asset_ids"]
                    and d["owner"] in state.scenario["owners"]
                ):
                    represented.append(d["owner"])
        institutional_vote = False
        for group_id, group in state.objects.items():
            prior = before.objects.get(group_id, {})
            rule = prior.get("rule") if prior.get("kind") == "group" else None
            if not rule or slot not in prior["members"] or rule["expiry_state"] < state.tick + 1:
                continue
            assets = {"budget-" + p for p in state.scenario["owners"]}
            if not assets <= set(rule["authorized_asset_ids"]) or not set(
                state.scenario["owners"]
            ) <= set(prior["members"]):
                continue
            if choice["cost"] * 1000 > rule["spend_limit_milli"]:
                continue
            institutional_vote = True
            ballots = group.setdefault("allocation_ballots", {}).setdefault(
                str(state.scenario["window"]), {}
            )
            voters = (
                voting_authority(before, group_id, slot, prior["members"])
                if rule["decision_method"] == "delegated"
                else {slot}
            )
            for voter in voters:
                ballots[voter] = op["opportunity_id"]
            yes = {p for p, c in ballots.items() if c == op["opportunity_id"]}
            quorum = (
                len(ballots) * rule["quorum_denominator"]
                >= len(prior["members"]) * rule["quorum_numerator"]
            )
            if rule["decision_method"] in {"authority", "auction"}:
                approved = authority(before, group_id, rule, prior["members"]) in yes
            elif rule["decision_method"] == "rotation":
                members = sorted(prior["members"])
                approved = (
                    members[(state.tick - prior.get("rule_adopted_state", 0)) % len(members)] in yes
                )
            elif rule["decision_method"] == "unanimity":
                approved = set(prior["members"]) <= yes
            else:
                approved = len(yes) > len(prior["members"]) // 2
            if quorum and approved:
                represented.extend(state.scenario["owners"])
        require(represented or institutional_vote, "unauthorized_budget")
        for owner in set(represented):
            share = state.scenario["shares"][state.scenario["owners"].index(owner)]
            payment = choice["cost"] * share * 50
            extra = max(0, payment - w["reserves"].get(owner, 0))
            require(allowance[owner].get("budget", 0) >= extra, "budget_unavailable")
            allowance[owner]["budget"] -= extra
            w["reserves"][owner] = payment
            w["signatures"][owner] = op["opportunity_id"]
        return [
            event(
                "allocation_signed",
                dict(choice=op["opportunity_id"], represented=represented),
                slot,
            )
        ]

    def evolve(self, state, before):
        s = state.scenario
        w = s["windows"][s["window"]]
        events = []
        signatures = w["signatures"]
        if (
            w["executed"] is None
            and all(p in signatures for p in s["owners"])
            and len(set(signatures.values())) == 1
        ):
            idx = int(next(iter(signatures.values())).split("-")[-1])
            c = w["choices"][idx]
            for owner in s["owners"]:
                payment = w["reserves"][owner]
                require(
                    state.agents[owner]["inventory"].get("budget", 0) >= payment,
                    "broken_allocation_reserve",
                )
                state.agents[owner]["inventory"]["budget"] -= payment
            w["reserves"] = {}
            w["executed"] = idx
            events.append(
                event("allocation_committed", dict(window=s["window"], choice=c), phase="evolve")
            )
        value = w["choices"][w["executed"]]["benefit"] if w["executed"] is not None else 0
        events.append(sample(state, value, w["maximum"]))
        if (state.tick + 1) % 40 == 0:
            result = dict(
                window=s["window"],
                value=value,
                maximum=w["maximum"],
                executed=w["executed"] is not None,
            )
            s["results"].append(result)
            events.append(
                event("service_verified", result, visibility=["evaluator"], phase="evolve")
            )
            events.append(
                event(
                    "budget_retired",
                    {p: state.agents[p]["inventory"].get("budget", 0) for p in state.agents},
                    visibility=["evaluator"],
                    phase="evolve",
                )
            )
            w["reserves"] = {}
            for agent in state.agents.values():
                agent["inventory"].pop("budget", None)
            if state.tick + 1 < 240:
                s["window"] += 1
                self.fund(state)
                events.append(
                    event(
                        "budget_issued",
                        dict(window=s["window"], quantity_milli=20000),
                        phase="evolve",
                    )
                )
        if state.tick + 1 == 120:
            events.append(replace_slot(state, s["turnover_slot"]))
        return events
