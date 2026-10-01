"""Metric values with fixed denominators and canonical event evidence."""

from statistics import fmean

from ..core.timing import scaled


def clip(value):
    return max(0, min(1, value))


def normalize(value, low, high):
    if high <= low:
        raise ValueError("invalid_utility_bounds")
    return clip((value - low) / (high - low))


def extract(store, detail=False):
    """Metrics of a completed episode; `detail` adds the contribution of every scoring event."""
    store.verify()
    if store.status()["status"] != "completed":
        raise ValueError("incomplete_episode")
    if store.manifest["mode"] != "core":
        raise ValueError("core_mode_required_for_usi")
    domain = store.manifest["domain"]
    horizon = store.manifest["ticks"]
    width = scaled(horizon, 10)
    closing = scaled(horizon, 200)
    recovery_span = max(2, scaled(horizon, 5))

    shock = store.snapshot(0)["scenario"]["shock_tick"]

    def rows(*kinds):
        return list(store.events(kinds=list(kinds)))

    metrics = {}

    def add(name, values, denominator, events, labels=None):
        """`values[i]` is what `events[i]` contributed; one event may back a derived value."""
        if denominator <= 0 or len(values) != denominator:
            raise ValueError("invalid_metric_denominator")
        numerator = sum(values)
        metrics[f"{domain}.{name}"] = dict(
            normalized_value=clip(numerator / denominator),
            raw_value=numerator / denominator,
            limits=[0, 1],
            numerator=numerator,
            denominator=denominator,
            evidence_event_ids=sorted({e["seq"] for e in events}),
            extractor_version="usi-extractors-v1",
        )
        if detail:
            paired = events if len(events) == len(values) else [events[-1]] * len(values)
            metrics[f"{domain}.{name}"]["contributions"] = [
                dict(
                    seq=e["seq"],
                    tick=e["tick"],
                    value=float(v),
                    label=labels[i] if labels else e["type"],
                )
                for i, (e, v) in enumerate(zip(paired, values))
            ]

    if domain == "D1":
        outcomes = rows("probe_resolved")
        predictions, sources, labels = [], [], []
        for e in outcomes:
            r = e["payload"]
            for key, target in [(f"fact-{r['window']}", r["target"])] + (
                [(f"choice-{r['window']}", r["choice_target"])] if r["window"] % 2 == 0 else []
            ):
                p = r["forecasts"].get(key)
                predictions.append(
                    1 - 0.5 * sum((v - (k == target)) ** 2 for k, v in enumerate(p))
                    if p is not None and target is not None
                    else 0
                )
                sources.append(e)
                labels.append(key)
        add("prediction", predictions, 18, sources, labels)
        metrics["D1.prediction"].update(
            raw_brier=2 * (1 - sum(predictions) / 18),
            uniform_reference=(12 * 0.75 + 6 * (2 / 3)) / 18,
        )
        windows = [f"window {e['payload']['window']}" for e in outcomes]
        correct = [e["payload"]["decision"] == e["payload"]["target"] for e in outcomes]
        add("decision", correct, 12, outcomes, windows)
        updates = [e for e in outcomes if e["payload"]["update"]]
        add(
            "update",
            [e["payload"]["decision"] == e["payload"]["target"] for e in updates],
            6,
            updates,
            [f"window {e['payload']['window']}" for e in updates],
        )
    elif domain == "D2":
        events = rows("market_closed")
        outcomes = [e["payload"] for e in events]
        labels = [f"market {r['window']}" for r in outcomes]
        add(
            "private_gain",
            [normalize(r["utility"], *r["bounds"]) for r in outcomes],
            6,
            events,
            labels,
        )
        add(
            "joint_gain",
            [clip(r["joint_surplus"] / r["gmax"]) for r in outcomes],
            6,
            events,
            labels,
        )
        add("delivery", [clip(r["delivered"] / r["required"]) for r in outcomes], 6, events, labels)
        metrics["D2.private_gain"].update(
            raw_utilities=[r["utility"] for r in outcomes],
            utility_bounds=[r["bounds"] for r in outcomes],
            surplus=[r["utility"] - r["batna"] for r in outcomes],
        )
        metrics["D2.joint_gain"]["raw_surplus"] = [r["joint_surplus"] for r in outcomes]
    elif domain == "D3":
        events = rows("service_verified")
        outcomes = [e["payload"] for e in events]
        services = rows("service_sampled")
        costs = rows("task_contributed")
        add(
            "completion",
            [r["success"] for r in outcomes],
            4,
            events,
            [f"service {r['delivery']}" for r in outcomes],
        )
        add(
            "timeliness",
            [clip(e["payload"]["value"] / e["payload"]["target"]) for e in services],
            horizon,
            services,
        )


        bounds = store.snapshot(0)["scenario"]["efficiency_bounds"]
        delivered = [int(r["success"]) for r in outcomes]
        minimum = next(row["minimum_cost"] for row in bounds if row["services"] == delivered)
        spent = sum(e["payload"]["cost"] for e in costs)
        add(
            "efficiency",
            [clip(minimum / spent) if any(delivered) and spent else 0],
            1,
            costs or events,
            [f"minimum cost {minimum} over {spent} spent"],
        )
    elif domain == "D4":
        services = rows("service_sampled")
        stocks = rows("stock_snapshot")
        focal_values = [
            clip(e["payload"]["focal_consumed"] / e["payload"]["focal_demand"])
            for e in services
        ]
        values = [
            min(own, clip(e["payload"]["value"] / e["payload"]["target"]))
            for own, e in zip(focal_values, services)
        ]
        add("service", values, horizon, services)
        windows = {start: fmean(values[start : start + width]) for start in range(closing, horizon, width)}
        worst = min(windows, key=windows.get)
        add(
            "continuity",
            [windows[worst]],
            1,
            services[worst : worst + width],
            [f"worst closing window: ticks {worst + 1}-{worst + width}"],
        )
        last = stocks[-1]["payload"]
        add(
            "reserve",
            [clip(last["stock"] / last["reserve_target"]) * fmean(focal_values[-scaled(horizon, 40):])],
            1,
            stocks[-1:],
            [
                f"final stock {last['stock']} over target {last['reserve_target']}, weighted by closing focal service"
            ],
        )
        for metric in metrics.values():
            metric["attribution"] = "focal_and_collective"
            metric["extractor_version"] = "usi-extractors-v2-focal-commons"
    elif domain == "D5":
        events = rows("opportunity_closed", "dispute_resolved")
        outcomes = [e["payload"] for e in events]
        labels = [
            f"window {r['window']}" + (" (dispute)" if r["dispute"] else "") for r in outcomes
        ]
        add(
            "relational_utility",
            [normalize(r["utility"], *r["bounds"]) for r in outcomes],
            12,
            events,
            labels,
        )
        add("commitment_resolution", [r["credit"] for r in outcomes], 12, events, labels)
        disputes = [e for e in events if e["payload"]["dispute"]]
        add(
            "conflict_resolution",
            [normalize(e["payload"]["utility"], *e["payload"]["bounds"]) for e in disputes],
            4,
            disputes,
            [f"window {e['payload']['window']}" for e in disputes],
        )
    elif domain == "D6":
        events = rows("service_verified")
        outcomes = [e["payload"] for e in events]
        services = rows("service_sampled")
        labels = [f"window {r['window']}" for r in outcomes]
        add(
            "mandate_service",
            [clip(r["value"] / r["maximum"]) for r in outcomes],
            6,
            events,
            labels,
        )
        add("execution", [r["executed"] and r["value"] > 0 for r in outcomes], 6, events, labels)
        after = services[shock:]
        add(
            "turnover_service",
            [clip(e["payload"]["value"] / e["payload"]["target"]) for e in after],
            horizon - shock,
            after,
        )
    elif domain == "D7":
        events = rows("heldout_task_resolved")
        for name, kind, denom in [
            ("transfer_success", "transfer", 6),
            ("functional_reuse", "reuse", 6),
            ("retention", "retention", 3),
        ]:
            chosen = [e for e in events if e["payload"]["kind"] == kind]
            add(
                name,
                [e["payload"]["success"] for e in chosen],
                denom,
                chosen,
                [e["payload"]["task_id"] for e in chosen],
            )
    elif domain == "D8":
        services = rows("service_sampled")
        values = [clip(e["payload"]["value"] / e["payload"]["target"]) for e in services]
        add("postshock_service", values[shock:], horizon - shock, services[shock:])
        r = next(
            (
                start + 1
                for start in range(shock, horizon - recovery_span + 1)
                if all(v >= 0.8 for v in values[start : start + recovery_span])
            ),
            None,
        )
        add(
            "recovery",
            [1 - (r - shock - 1) / (horizon - 1 - shock) if r else 0],
            1,
            services[r - 1 : r - 1 + recovery_span] if r else services[-1:],
            [f"{recovery_span} good ticks from tick {r}" if r else "service never recovered"],
        )
        interactions = rows("interaction_verified")
        add(
            "convention_transfer",
            [e["payload"]["success"] for e in interactions],
            sum((tick + 1) % width == 0 for tick in range(shock + width - 1, horizon)),
            interactions,
            [f"interaction {e['payload']['interaction']}" for e in interactions],
        )
    return metrics
