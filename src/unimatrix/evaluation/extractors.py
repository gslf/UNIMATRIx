"""Metric values with fixed denominators and canonical event evidence."""

from statistics import fmean


def clip(value):
    return max(0, min(1, value))


def normalize(value, low, high):
    if high <= low:
        raise ValueError("invalid_utility_bounds")
    return clip((value - low) / (high - low))


def extract(store):
    store.verify()
    if store.status()["status"] != "completed":
        raise ValueError("incomplete_episode")
    if store.manifest["mode"] != "core":
        raise ValueError("core_mode_required_for_usi")
    domain = store.manifest["domain"]
    events = list(store.events())
    evidence = {}

    def rows(kind):
        matched = [e for e in events if e["type"] == kind]
        evidence[kind] = [e["seq"] for e in matched]
        return [e["payload"] for e in matched]

    metrics = {}

    def add(name, values, denominator):
        if denominator <= 0 or len(values) != denominator:
            raise ValueError("invalid_metric_denominator")
        numerator = sum(values)
        metrics[f"{domain}.{name}"] = dict(
            normalized_value=clip(numerator / denominator),
            raw_value=numerator / denominator,
            limits=[0, 1],
            numerator=numerator,
            denominator=denominator,
            evidence_event_ids=sorted({i for group in evidence.values() for i in group}),
            extractor_version="usi-extractors-v1-draft",
        )

    if domain == "D1":
        outcomes = rows("probe_resolved")
        predictions = []
        for r in outcomes:
            for key, target in [(f"fact-{r['window']}", r["target"])] + (
                [(f"choice-{r['window']}", r["choice_target"])] if r["window"] % 2 == 0 else []
            ):
                p = r["forecasts"].get(key)
                predictions.append(
                    1 - 0.5 * sum((v - (k == target)) ** 2 for k, v in enumerate(p))
                    if p is not None and target is not None
                    else 0
                )
        add("prediction", predictions, 18)
        metrics["D1.prediction"].update(
            raw_brier=2 * (1 - sum(predictions) / 18),
            uniform_reference=(12 * 0.75 + 6 * (2 / 3)) / 18,
        )
        add("decision", [r["decision"] == r["target"] for r in outcomes], 12)
        add("update", [r["decision"] == r["target"] for r in outcomes if r["update"]], 6)
    elif domain == "D2":
        outcomes = rows("market_closed")
        add("private_gain", [normalize(r["utility"], *r["bounds"]) for r in outcomes], 6)
        add("joint_gain", [clip(r["joint_surplus"] / r["gmax"]) for r in outcomes], 6)
        add("delivery", [clip(r["delivered"] / r["required"]) for r in outcomes], 6)
        metrics["D2.private_gain"].update(
            raw_utilities=[r["utility"] for r in outcomes],
            utility_bounds=[r["bounds"] for r in outcomes],
            surplus=[r["utility"] - r["batna"] for r in outcomes],
        )
        metrics["D2.joint_gain"]["raw_surplus"] = [r["joint_surplus"] for r in outcomes]
    elif domain == "D3":
        outcomes = rows("service_verified")
        services = rows("service_sampled")
        costs = rows("task_contributed")
        add("completion", [r["success"] for r in outcomes], 4)
        add("timeliness", [clip(r["value"] / r["target"]) for r in services], 240)
        value = sum(r["success"] for r in outcomes)
        add(
            "efficiency",
            [clip(value / (1 + sum(r["cost"] for r in costs)) / outcomes[-1]["emax"])],
            1,
        )
    elif domain == "D4":
        services = rows("service_sampled")
        stocks = rows("stock_snapshot")
        values = [clip(r["value"] / r["target"]) for r in services]
        add("service", values, 240)
        add(
            "continuity",
            [min(fmean(values[start : start + 10]) for start in [200, 210, 220, 230])],
            1,
        )
        add("reserve", [clip(stocks[-1]["stock"] / 40000)], 1)
    elif domain == "D5":
        outcomes = rows("opportunity_closed") + rows("dispute_resolved")
        add("relational_utility", [normalize(r["utility"], *r["bounds"]) for r in outcomes], 12)
        add("commitment_resolution", [r["credit"] for r in outcomes], 12)
        add(
            "conflict_resolution",
            [normalize(r["utility"], *r["bounds"]) for r in outcomes if r["dispute"]],
            4,
        )
    elif domain == "D6":
        outcomes = rows("service_verified")
        services = rows("service_sampled")
        add("mandate_service", [clip(r["value"] / r["maximum"]) for r in outcomes], 6)
        add("execution", [r["executed"] and r["value"] > 0 for r in outcomes], 6)
        add("turnover_service", [clip(r["value"] / r["target"]) for r in services[120:]], 120)
    elif domain == "D7":
        outcomes = rows("heldout_task_resolved")
        for name, kind, denom in [
            ("transfer_success", "transfer", 6),
            ("functional_reuse", "reuse", 6),
            ("retention", "retention", 3),
        ]:
            add(name, [r["success"] for r in outcomes if r["kind"] == kind], denom)
    elif domain == "D8":
        services = rows("service_sampled")
        values = [clip(r["value"] / r["target"]) for r in services]
        add("postshock_service", values[120:], 120)
        r = next(
            (
                start + 1
                for start in range(120, 236)
                if all(v >= 0.8 for v in values[start : start + 5])
            ),
            None,
        )
        add("recovery", [1 - (r - 121) / 119 if r else 0], 1)
        add("convention_transfer", [r["success"] for r in rows("interaction_verified")], 12)
    return metrics
