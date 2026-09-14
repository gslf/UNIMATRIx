"""Distributed noisy signals, sealed forecasts and material routing choices."""

import math

from ..core.random_tape import RandomTape
from ..core.state import event
from ..world.contracts import require
from .base import Scenario


class Information(Scenario):
    domain = "D1"

    def build(self, manifest):
        state = super().build(manifest)
        tape = RandomTape(manifest["seed"])
        sources = [s for s in manifest["slots"] if s != manifest["focal_slot"]][:3]
        windows = []
        for w in range(12):
            target = tape.integer(w, "condition", "truth", 2)
            signals = {}
            for source, accuracy in zip(sources, [60, 75, 90]):
                if manifest["level"] == 3 and w >= 6 and source == sources[2]:
                    accuracy = 40
                signals[source] = (
                    target if tape.integer(w, source, "noise", 100) < accuracy else 1 - target
                )
            windows.append(
                dict(
                    target=target,
                    signals=signals,
                    choices={},
                    choice_ticks={},
                    forecasts={},
                    inspected=[],
                    due=(w + 1) * 20,
                )
            )
        state.scenario.update(windows=windows, window=0, results=[])
        for slot in state.agents:
            state.agents[slot]["inventory"] = dict(
                energy=12000 if manifest["role"] == "advantaged" else 6000
            )
        return state

    def observation(self, state, slot):
        w = state.scenario["window"]
        window = state.scenario["windows"][w]
        focal = state.scenario["focal"]
        local = state.tick % 20
        probes = []
        if slot == focal and local == 18:
            probes = [dict(id=f"fact-{w}", classes=2)]
            if w % 2 == 0:
                probes.append(dict(id=f"choice-{w}", classes=3))
        signal = window["signals"].get(slot)
        if slot == focal and local >= 10 and w % 2 == 1:
            signal = window["target"]
        return dict(
            domain=self.domain,
            window=w,
            due=window["due"],
            signal=signal,
            probes=probes,
            evidence_certified=slot == focal and local >= 10 and w % 2 == 1,
            sources=list(window["signals"]),
            inspect_target="signal",
            choices=[f"route-{i}" for i in range(3)],
            operations=["inspect", "work"],
            available_actions=[],
        )

    def forecasts(self, state, before, slot, forecasts):
        if slot != state.scenario["focal"] or state.tick % 20 != 18:
            return []
        w = state.scenario["window"]
        window = state.scenario["windows"][w]
        events = []
        seen = set()
        for forecast in forecasts:
            key = forecast["probe_id"]
            size = 2 if key == f"fact-{w}" else 3 if w % 2 == 0 and key == f"choice-{w}" else 0
            p = forecast["probabilities"]
            valid = (
                size
                and len(p) == size
                and all(math.isfinite(v) for v in p)
                and abs(sum(p) - 1) <= 1e-6
            )
            if key in seen:
                window["forecasts"][key] = None
            elif valid:
                window["forecasts"][key] = p
            seen.add(key)
            events.append(
                event(
                    "forecast_submitted",
                    dict(probe_id=key, probabilities=p, valid=bool(valid)),
                    slot,
                    [slot],
                )
            )
        return events

    def resolve(self, state, before, allowance, slot, op, ident):
        window = state.scenario["windows"][state.scenario["window"]]
        if op["verb"] == "work":
            require(op["project_id"] in {"route-0", "route-1", "route-2"}, "unknown_route")
            window["choices"][slot] = int(op["project_id"][-1])
            window["choice_ticks"][slot] = state.tick
            return [event("supply_routed", dict(route=window["choices"][slot]), slot, [slot])]
        if op["verb"] == "inspect" and op["target_id"] == "signal":
            from ..world.contracts import debit

            debit(state, allowance, slot, "energy", 1000)
            window["inspected"].append(slot)
            # Access buys a noisy source's actual signal, not the ground truth.
            source = sorted(window["signals"])[0]
            return [
                event(
                    "evidence_observed",
                    dict(signal=window["signals"][source], source=source),
                    slot,
                    [slot],
                )
            ]
        return super().resolve(state, before, allowance, slot, op, ident)

    def evolve(self, state, before):
        window = state.scenario["windows"][state.scenario["window"]]
        events = []
        if state.tick % 20 == 17:
            events.append(
                event(
                    "probe_issued",
                    dict(window=state.scenario["window"], sealed_at=state.tick + 2),
                    visibility=[state.scenario["focal"]],
                    phase="observe",
                )
            )
        if state.tick % 20 == 9 and state.scenario["window"] % 2 == 1:
            events.append(
                event(
                    "evidence_observed",
                    dict(signal=window["target"], certified=True),
                    visibility=[state.scenario["focal"]],
                    phase="observe",
                )
            )
        if state.tick % 20 == 18:
            other = sorted(window["signals"])[0]
            window["choice_target"] = (
                window["choices"].get(other, 2)
                if window["choice_ticks"].get(other) == state.tick
                else 2
            )
        if (state.tick + 1) % 20:
            return events
        w = state.scenario["window"]
        result = dict(
            window=w,
            target=window["target"],
            choice_target=window.get("choice_target"),
            forecasts=window["forecasts"],
            decision=window["choices"].get(state.scenario["focal"]),
            update=w % 2 == 1,
        )
        state.scenario["results"].append(result)
        events.append(event("probe_resolved", result, visibility=["evaluator"], phase="evolve"))
        if state.tick + 1 < 240:
            state.scenario["window"] += 1
        return events
