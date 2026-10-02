"""Distributed noisy signals, sealed forecasts and material routing choices."""

import math

from ..core.random_tape import RandomTape, noise_seed
from ..core.state import event
from ..core.timing import span
from ..world.contracts import require
from .base import Scenario, capable_peers
from .layers import layer


class Information(Scenario):
    domain = "D1"

    def populate(self, state, manifest):
        tape = RandomTape(manifest["seed"])

        noise = RandomTape(noise_seed(manifest["seed"], manifest["replicate"]))

        sources = capable_peers(manifest)[:3][::-1]
        drift = state.scenario["shock_tick"] // span(state, 20)
        windows = []
        for w in range(12):
            target = tape.integer(w, "condition", "truth", 2)
            signals = {}
            for source, accuracy in zip(sources, [60, 75, 90]):
                if layer(state, "noise", "source_drift") and w >= drift and source == sources[2]:
                    accuracy = 40
                signals[source] = (
                    target if noise.integer(w, source, "noise", 100) < accuracy else 1 - target
                )
            window = dict(
                target=target,
                signals=signals,
                choices={},
                choice_ticks={},
                forecasts={},
                inspected=[],
                due=(w + 1) * span(state, 20),
            )
            if not layer(state, "information", "certified_evidence") and w % 2 == 1:

                noisy = noise.integer(w, "second", "noise", 100) >= 75
                window["second"] = 1 - target if noisy else target
            if layer(state, "information", "inspect_best_source"):
                noisy = noise.integer(w, "inspection", "noise", 100) >= 90
                window["inspection"] = 1 - target if noisy else target
            windows.append(window)
        state.scenario.update(windows=windows, window=0, results=[], accuracies=dict(zip(sources, [60, 75, 90])))
        for slot in state.agents:
            state.agents[slot]["inventory"] = dict(
                energy=12000 if manifest["role"] == "advantaged" else 6000
            )

    def structure(self, state):
        s = state.scenario
        sources = s["accuracies"]
        return dict(
            positions={slot: [f"signal source ({accuracy}% accurate)"] for slot, accuracy in sources.items()}
            | {s["focal"]: ["forecaster and router"]},
            knowledge={slot: ["a private noisy signal per window"] for slot in sources}
            | {s["focal"]: ["sealed probes", "mid-window evidence in odd windows"]},
            ties=[(slot, s["focal"], "broadcast signal") for slot in sources],
            interests={slot: "route supplies to the true condition" for slot in state.agents},
        )

    def gauges(self, state):
        return dict(super().gauges(state), window=state.scenario["window"])

    def observation(self, state, slot):
        w = state.scenario["window"]
        window = state.scenario["windows"][w]
        focal = state.scenario["focal"]
        local = state.tick % span(state, 20)
        probes = []
        if slot == focal and local == span(state, 20) - 2:
            probes = [dict(
                id=f"fact-{w}", classes=2, outcomes=[0, 1],
                target=dict(kind="hidden_fact", window=w),
            )]
            if w % 2 == 0:
                probes.append(dict(
                    id=f"choice-{w}", classes=3,
                    outcomes=["route-0", "route-1", "route-2 or no route in scope"],
                    target=dict(
                        kind="agent_route", agent_id=min(window["signals"]), tick=state.tick,
                        scope=("current_window" if layer(state, "information", "choice_probe")
                               == "last_route" else "current_tick"),
                    ),
                ))
        signal = window["signals"].get(slot)
        certified = layer(state, "information", "certified_evidence")
        if slot == focal and local >= span(state, 10) and w % 2 == 1:
            signal = window["target"] if certified else window["second"]
        return dict(
            domain=self.domain,
            window=w,
            window_ticks=span(state, 20),
            forecast_offset=span(state, 20) - 2,
            due=window["due"],
            signal=signal,
            probes=probes,
            evidence_certified=slot == focal and local >= span(state, 10) and w % 2 == 1 and certified,
            sources=sorted(window["signals"]),
            inspect_target="signal",
            choices=[f"route-{i}" for i in range(3)],
            operations=["inspect", "work"],
            available_actions=[],
        )

    def forecasts(self, state, before, slot, forecasts):
        if slot != state.scenario["focal"] or state.tick % span(state, 20) != span(state, 20) - 2:
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

            if "inspection" in window:


                source = min(window["signals"], key=lambda s: (-state.scenario["accuracies"][s], s))
                signal = window["inspection"]
            else:
                source = sorted(window["signals"])[0]
                signal = window["signals"][source]
            return [
                event(
                    "evidence_observed",
                    dict(signal=signal, source=source),
                    slot,
                    [slot],
                )
            ]
        return super().resolve(state, before, allowance, slot, op, ident)

    def evolve(self, state, before):
        window = state.scenario["windows"][state.scenario["window"]]
        events = []
        if state.tick % span(state, 20) == span(state, 20) - 3:
            events.append(
                event(
                    "probe_issued",
                    dict(window=state.scenario["window"], sealed_at=state.tick + 2),
                    visibility=[state.scenario["focal"]],
                    phase="observe",
                )
            )
        if state.tick % span(state, 20) == span(state, 10) - 1 and state.scenario["window"] % 2 == 1:
            certified = layer(state, "information", "certified_evidence")
            events.append(
                event(
                    "evidence_observed",
                    dict(
                        signal=window["target"] if certified else window["second"],
                        certified=certified,
                    ),
                    visibility=[state.scenario["focal"]],
                    phase="observe",
                )
            )
        if state.tick % span(state, 20) == span(state, 20) - 2:
            other = sorted(window["signals"])[0]
            if layer(state, "information", "choice_probe") == "last_route":
                window["choice_target"] = window["choices"].get(other, 2)
            else:
                window["choice_target"] = (
                    window["choices"].get(other, 2)
                    if window["choice_ticks"].get(other) == state.tick
                    else 2
                )
        if (state.tick + 1) % span(state, 20):
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
        if state.tick + 1 < state.scenario["horizon"]:
            state.scenario["window"] += 1
        return events
