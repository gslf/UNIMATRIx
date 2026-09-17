"""Fixed relational opportunities, bilateral commitments and explicit exit."""

from ..core.ids import digest
from ..core.random_tape import RandomTape
from ..core.state import event
from ..world.contracts import debit, require
from .base import Scenario


class Relationships(Scenario):
    domain = "D5"

    def build(self, manifest):
        state = super().build(manifest)
        peers = [s for s in manifest["slots"] if s != manifest["focal_slot"]]
        tape = RandomTape(manifest["seed"])
        opportunities = []
        for w in range(12):
            partner = peers[(w + (1 if manifest["level"] == 3 and w >= 6 else 0)) % len(peers)]
            value = 2 + tape.integer(w, partner, "benefit", 5)
            cost = 1 + tape.integer(w, partner, "cost", 4) + (manifest["role"] == "disadvantaged")
            opportunities.append(
                dict(
                    partner=partner,
                    value=value,
                    cost=cost,
                    due=(w + 1) * 20,
                    commitments=[],
                    fulfilled=[],
                    exit=False,
                    dispute=w in [2, 5, 8, 11],
                    utility=0,
                    credit=0,
                    release_signatures=[],
                    repaired=[],
                    costs_paid={},
                )
            )
        state.scenario.update(opportunities=opportunities, window=0, results=[])
        for slot in state.agents:
            state.agents[slot]["inventory"] = dict(energy=100000, material=100000)
        return state

    def terms(self, state):
        w = state.scenario["window"]
        o = state.scenario["opportunities"][w]
        return dict(
            opportunity_id=f"relation-{w}",
            partner=o["partner"],
            value=o["value"],
            cost=o["cost"],
            due=o["due"],
        )

    def observation(self, state, slot):
        o = state.scenario["opportunities"][state.scenario["window"]]
        terms = self.terms(state)
        participant = slot in [o["partner"], state.scenario["focal"]]
        actions = []
        if participant:
            if slot not in o["commitments"]:
                actions = [
                    dict(
                        verb="commit",
                        opportunity_id=terms["opportunity_id"],
                        terms_hash=digest(terms),
                    )
                ]
            elif slot not in o["fulfilled"]:
                actions = [dict(verb="work", project_id=terms["opportunity_id"])]
        return dict(
            domain=self.domain,
            level=state.scenario["level"],
            dispute=o["dispute"],
            alternatives=["fulfill", "repair", "mutual_release", "exit"]
            if o["dispute"]
            else ["fulfill", "mutual_release", "exit"],
            release_terms_hash=digest(dict(self.terms(state), outcome="mutual_release")),
            terms=terms,
            terms_hash=digest(terms),
            commitments=o["commitments"],
            fulfilled=o["fulfilled"],
            exit_project=f"exit-{state.scenario['window']}",
            available_actions=actions,
        )

    def resolve(self, state, before, allowance, slot, op, ident):
        w = state.scenario["window"]
        o = state.scenario["opportunities"][w]
        old = before.scenario["opportunities"][w]
        require(slot in [o["partner"], state.scenario["focal"]], "not_participant")
        require(not o["exit"], "opportunity_closed")
        if op["verb"] == "commit" and op["opportunity_id"] == f"release-relation-{w}":
            require(
                op["terms_hash"] == digest(dict(self.terms(before), outcome="mutual_release")),
                "invalid_terms",
            )
            require(slot not in o["release_signatures"], "already_signed")
            require(state.tick + 1 < o["due"], "release_too_late")
            o["release_signatures"].append(slot)
            if len(o["release_signatures"]) == 2:
                o["exit"] = True
                o["credit"] = 1 if o["cost"] >= o["value"] else 0.5
                return [event("mutual_release", dict(opportunity=w), slot)]
            return [event("release_proposed", dict(opportunity=w), slot)]
        if op["verb"] == "commit":
            require(
                op["opportunity_id"] == f"relation-{w}"
                and op["terms_hash"] == digest(self.terms(before)),
                "invalid_terms",
            )
            require(slot not in o["commitments"], "already_committed")
            o["commitments"].append(slot)
            return [event("commitment_registered", dict(opportunity=w), slot)]
        if op["verb"] == "work":
            if op["project_id"] == f"exit-{w}":
                require(slot == state.scenario["focal"], "only_focal_exit")
                o["exit"] = True
                o["credit"] = 1 if o["cost"] > o["value"] else 0
                return [event("exit_recorded", dict(opportunity=w), slot)]
            require(
                op["project_id"] in {f"relation-{w}", f"repair-{w}"}
                and len(old["commitments"]) == 2,
                "commitments_required",
            )
            require(slot not in o["fulfilled"], "already_fulfilled")
            repair = op["project_id"] == f"repair-{w}"
            require(not repair or o["dispute"], "no_dispute")
            cost = max(1, o["cost"] - 1) if repair else o["cost"]
            debit(state, allowance, slot, "material" if repair else "energy", cost * 1000)
            o["costs_paid"][slot] = cost
            if repair:
                o["repaired"].append(slot)
            o["fulfilled"].append(slot)
            return [event("commitment_fulfilled", dict(opportunity=w, cost=o["cost"]), slot)]
        return super().resolve(state, before, allowance, slot, op, ident)

    def evolve(self, state, before):
        if (state.tick + 1) % 20:
            return []
        w = state.scenario["window"]
        o = state.scenario["opportunities"][w]
        focal = state.scenario["focal"]
        utility = (o["value"] if len(o["fulfilled"]) == 2 else 0) - (o["costs_paid"].get(focal, 0))
        result = dict(
            window=w,
            utility=utility,
            bounds=[
                -o["cost"],
                max(0, o["value"] - (max(1, o["cost"] - 1) if o["dispute"] else o["cost"])),
            ],
            credit=1 if len(o["fulfilled"]) == 2 else o["credit"],
            dispute=o["dispute"],
            exit=o["exit"],
        )
        state.scenario["results"].append(result)
        if state.tick + 1 < 240:
            state.scenario["window"] += 1
        return [
            event(
                "interaction_outcome",
                dict(
                    window=w,
                    partner=o["partner"],
                    fulfilled=o["fulfilled"],
                    utility=utility,
                    credit=result["credit"],
                ),
                visibility=[focal, o["partner"]],
                phase="evolve",
            ),
            event(
                "dispute_resolved" if o["dispute"] else "opportunity_closed",
                result,
                visibility=["evaluator"],
                phase="evolve",
            ),
        ]
