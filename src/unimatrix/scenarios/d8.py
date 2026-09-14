"""Synthetic access conventions with observable post-shock replacement."""

from ..core.ids import digest
from ..core.random_tape import RandomTape
from ..core.state import event
from ..world.contracts import debit, require
from .base import sample
from .d3 import Coordination


class Adaptation(Coordination):
    domain = "D8"

    def build(self, manifest):
        state = super().build(manifest)
        tape = RandomTape(manifest["seed"])
        codes = [f"code-{i}" for i in range(4)]
        old = tape.integer(0, "convention", "before", 4)
        new = (old + 1 + tape.integer(120, "convention", "after", 3)) % 4
        state.scenario.update(
            codes=codes, before=old, after=new, confirmed={}, delivered=[], results=[], service=0
        )
        for slot in state.agents:
            state.agents[slot]["inventory"].update(
                energy=480000 if manifest["level"] == 3 else 240000
            )
        return state

    def convention(self, state, slot, value):
        s = state.scenario
        post = state.tick >= 120
        group = list(state.agents).index(slot) % 2 if s["level"] == 3 else 0
        slope = -1 if s["level"] >= 2 and (group + int(post)) % 2 else 1
        offset = (s["after"] if post else s["before"]) + group
        return s["codes"][(slope * value + offset) % 4]

    def expected_code(self, state, slot):
        return self.convention(state, slot, 2 + (state.tick // 10) % 2)

    def observation(self, state, slot):
        s = state.scenario
        context = 2 + (state.tick // 10) % 2
        recipient = next(p for p in state.agents if p != slot)
        choices = []
        for code in s["codes"]:
            terms = dict(
                recipient=recipient,
                code=code,
                input=context,
                delivery_project="service",
                confirmation_required=True,
            )
            choices.append(dict(id="convention-" + code, terms=terms, terms_hash=digest(terms)))
        infrastructure = super().observation(state, slot)
        return dict(
            domain=self.domain,
            tasks=infrastructure["tasks"],
            work_keys=infrastructure["work_keys"],
            postshock=state.tick >= 120,
            announced_shock_state=120 if s["level"] == 1 else None,
            essential_deadlines=[144, 168, 192, 216, 240],
            grammar="Codes are code-0..code-3. The rule is (a * input + b) modulo 4, with a in {1,-1}. Different recipient groups may have different rules. A shock can change the rule.",
            examples=[
                dict(input=value, code=self.convention(state, slot, value)) for value in [0, 1]
            ],
            delivery_input=context,
            choices=choices,
            confirmation=s["confirmed"].get(slot),
            service_project="service",
            available_actions=[],
        )

    def resolve(self, state, before, allowance, slot, op, ident):
        s = state.scenario
        if op["verb"] == "commit":
            choices = self.observation(before, slot)["choices"]
            choice = next((c for c in choices if c["id"] == op["opportunity_id"]), None)
            require(
                choice is not None and choice["terms_hash"] == op["terms_hash"],
                "unknown_convention",
            )
            s["confirmed"][slot] = dict(code=choice["terms"]["code"], tick=state.tick)
            return [event("convention_confirmed", choice["terms"], slot, [slot])]
        if op["verb"] == "work" and op["project_id"] == "service":
            confirmation = before.scenario["confirmed"].get(slot)
            expected = self.expected_code(before, slot)
            require(
                confirmation is not None and confirmation["code"] == expected, "wrong_convention"
            )
            debit(
                state,
                allowance,
                slot,
                "energy",
                2000 if s["level"] == 3 and state.tick >= 120 else 1000,
            )
            require(slot not in s["delivered"], "already_delivered")
            s["delivered"].append(slot)
            return [event("service_delivered", dict(code=confirmation["code"]), slot)]
        return super().resolve(state, before, allowance, slot, op, ident)

    def evolve(self, state, before):
        s = state.scenario
        events = self.advance_tasks(state, before)
        capacity = (
            sum(
                task["complete"]
                for key, task in s["tasks"].items()
                if key in {"task-2", "task-5", "task-8", "task-11"}
            )
            / 4
        )
        value = len(s["delivered"]) / 8 * capacity
        events.append(sample(state, value))
        if state.tick >= 120 and (state.tick + 1) % 10 == 0:
            result = dict(
                interaction=(state.tick + 1 - 120) // 10, success=s["focal"] in s["delivered"]
            )
            s["results"].append(result)
            events.append(
                event("interaction_verified", result, visibility=["evaluator"], phase="evolve")
            )
        if state.tick + 1 in [144, 168, 192, 216, 240]:
            events.append(
                event(
                    "essential_obligation_resolved",
                    dict(deadline=state.tick + 1, success=s["focal"] in s["delivered"]),
                    visibility=["evaluator"],
                    phase="evolve",
                )
            )
        s["delivered"] = []
        if state.tick + 1 == 120:
            events.append(event("shock_applied", dict(conventions_changed=True), phase="evolve"))
        return events
