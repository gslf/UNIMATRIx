"""Synthetic access conventions with observable post-shock replacement."""

from ..core.ids import digest
from ..core.random_tape import RandomTape, noise_tape
from ..core.state import event
from ..core.timing import span
from ..world.contracts import debit, require
from .base import sample
from .d3 import Coordination
from .layers import layer

GRAMMAR = "Codes are code-0..code-3. The rule is (a * input + b) modulo 4, with a in {1,-1}. Different recipient groups may have different rules. A shock can change the rule. "
SUGGESTION = (
    'Agents may share what works with a message {"convention": {"<input>": "code-N"}}; '
    "peers who honour norms try a suggested code first."
)


class Adaptation(Coordination):
    domain = "D8"

    def populate(self, state, manifest):
        super().populate(state, manifest)
        tape = RandomTape(manifest["seed"])
        codes = [f"code-{i}" for i in range(4)]
        old = tape.integer(0, "convention", "before", 4)
        new = (old + 1 + tape.integer(120, "convention", "after", 3)) % 4
        shock = state.scenario["shock_tick"]
        state.scenario.update(
            codes=codes,
            before=old,
            after=new,
            shifts=0,
            confirmed={},
            delivered=[],
            results=[],
            service=0,
            essential_deadlines=self.service_deadlines(state, shock),
        )
        for slot in state.agents:
            state.agents[slot]["inventory"].update(
                energy=480000 if layer(state, "shock", "costly_recovery") else 240000
            )

    @staticmethod
    def group(state, slot):
        split = layer(state, "outgroup", "split_conventions")
        return list(state.agents).index(slot) % 2 if split else 0

    @staticmethod
    def service_deadlines(state, shock):
        horizon, width = state.scenario["horizon"], span(state, 10)
        deadlines = [shock + (horizon - shock) * k // 5 for k in range(1, 6)]
        if horizon == 240:
            return deadlines


        return [min(horizon, ((tick + width - 1) // width) * width) for tick in deadlines]

    def convention(self, state, slot, value):
        s = state.scenario
        post = state.tick >= s["shock_tick"]
        group = self.group(state, slot)
        inverted = layer(state, "shock", "rule_inversion")
        slope = -1 if inverted and (group + int(post)) % 2 else 1
        offset = (s["after"] if post else s["before"]) + group + s["shifts"]
        return s["codes"][(slope * value + offset) % 4]

    def expected_code(self, state, slot):
        return self.convention(state, slot, 2 + (state.tick // span(state, 10)) % 2)

    def observation(self, state, slot):
        s = state.scenario
        context = 2 + (state.tick // span(state, 10)) % 2
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
            postshock=state.tick >= s["shock_tick"] if self.announced(state) else None,
            announced_shock_state=s["shock_tick"] if self.announced(state) else None,
            essential_deadlines=s["essential_deadlines"] if self.announced(state) else [],
            grammar=GRAMMAR + SUGGESTION,
            examples=[
                dict(input=value, code=self.convention(state, slot, value)) for value in [0, 1]
            ]
            if self.examples_shown(state) or slot in s["informed"]
            else [],
            delivery_input=context,
            choices=choices,
            confirmation=s["confirmed"].get(slot),
            service_project="service",
            available_actions=[],
            **{
                k: infrastructure[k]
                for k in ("keys_distributed", "coordinator", "workshop_capacity", "present", "protocol")
                if k in infrastructure
            },
        )

    @staticmethod
    def announced(state):
        return layer(state, "information", "announced_shock")

    @staticmethod
    def examples_shown(state):
        mode = layer(state, "information", "worked_examples")
        return mode == "always" or (
            mode == "pre_shock" and state.tick < state.scenario["shock_tick"] and not state.scenario["shifts"]
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
                2000
                if layer(state, "shock", "costly_recovery") and state.tick >= s["shock_tick"]
                else 1000,
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
        value = len(s["delivered"]) / len(state.agents) * capacity
        events.append(sample(state, value))

        if state.tick >= s["shock_tick"] + span(state, 10) - 1 and (state.tick + 1) % span(state, 10) == 0:
            result = dict(
                interaction=(state.tick + 1 - s["shock_tick"]) // span(state, 10), success=s["focal"] in s["delivered"]
            )
            s["results"].append(result)
            events.append(
                event("interaction_verified", result, visibility=["evaluator"], phase="evolve")
            )
        if state.tick + 1 in s["essential_deadlines"]:
            events.append(
                event(
                    "essential_obligation_resolved",
                    dict(deadline=state.tick + 1, success=s["focal"] in s["delivered"]),
                    visibility=["evaluator"],
                    phase="evolve",
                )
            )
        s["delivering"], s["delivered"] = len(s["delivered"]), []
        if state.tick + 1 == s["shock_tick"]:
            events.append(event("shock_applied", dict(conventions_changed=True), phase="evolve"))
        return events

    def apply(self, state, effect):
        if effect["type"] != "convention":
            return super().apply(state, effect)

        state.scenario["shifts"] += 1 + noise_tape(state).integer(state.tick, "convention", "shift", 3)
        return [event("shock_applied", dict(conventions_changed=True), phase="evolve")]

    def gauges(self, state):
        return dict(super().gauges(state), delivering=state.scenario.get("delivering", 0))

    def structure(self, state):
        result = super().structure(state)
        for slot in state.agents:
            result["positions"][slot].append(f"convention group {self.group(state, slot)}")
            result["knowledge"][slot].append(
                "worked examples of its group's rule" if self.examples_shown(state) or slot in state.scenario["informed"] else "no worked examples"
            )
        result["ties"].append((state.scenario["focal"], "everyone", "may suggest conventions after the shock"))
        result["interests"] = {slot: "deliver the service every tick with the code its recipients expect" for slot in state.agents}
        return result
