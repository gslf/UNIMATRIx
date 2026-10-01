"""Metric debugger: what a recipe's metrics reward, with the evidence behind every score."""

import math
from pathlib import Path
from statistics import fmean, pvariance

from ..evaluation.extractors import extract
from ..evaluation.scoring import case_key
from ..persistence.event_store import EventStore
from ..persistence.json_files import read_json

FLOORS = ("baseline/random", "baseline/passive")
CEILINGS = ("baseline/oracle", "baseline/coordinator", "baseline/reciprocal")
INERT_EVENTS = ("decision_recorded", "message_sent", "operation_rejected", "decision_rejected")


def correlation(left, right):
    if len(left) < 8 or pvariance(left) == 0 or pvariance(right) == 0:
        return None
    a, b = fmean(left), fmean(right)
    covariance = sum((x - a) * (y - b) for x, y in zip(left, right)) / len(left)
    return covariance / math.sqrt(pvariance(left) * pvariance(right))


def activity(path, focal):
    """What the candidate did in one episode: messages sent and effects caused."""
    store = EventStore(path, read_only=True)
    try:
        rows = dict(
            store.db.execute(
                "SELECT type, COUNT(*) FROM events WHERE json_extract(body,'$.actor_id')=? GROUP BY type",
                (focal,),
            ).fetchall()
        )
    finally:
        store.close()
    return dict(
        messages=rows.get("message_sent", 0),
        rejected=rows.get("operation_rejected", 0) + rows.get("decision_rejected", 0),
        effects=sum(n for kind, n in rows.items() if kind not in INERT_EVENTS),
    )


def explain_episode(path, weights):
    """Every metric of one episode with the events that produced it."""
    store = EventStore(Path(path), read_only=True)
    try:
        metrics = extract(store, detail=True)
        focal = store.manifest["focal_slot"]
    finally:
        store.close()
    rows = []
    for name, metric in metrics.items():
        contributions = metric["contributions"]
        rows.append(
            dict(
                metric=name,
                weight=weights.get(name),
                value=metric["normalized_value"],
                numerator=metric["numerator"],
                denominator=metric["denominator"],
                contributions=contributions,
                lost=sorted(contributions, key=lambda c: c["value"])[:5],
            )
        )
    return dict(
        focal=focal,
        score=sum(r["value"] * (r["weight"] or 0) for r in rows),
        metrics=rows,
    )


def metric_report(campaign, folder):
    """Per metric: who scores what, how much it really weighs, and signs of a loophole."""
    folder = Path(folder)
    observations = {}
    for study in campaign["studies"]:
        if study["status"] != "completed" or study["intervention"] is not None:
            continue
        data = read_json(folder / "studies" / study["id"] / "results.json")
        episodes = {case_key(m): m for m in study["execution"]["episodes"]}
        for row in data["runs"]:
            manifest = episodes[case_key(row)]
            path = folder / "studies" / study["id"] / "episodes" / manifest["run_id"] / "episode.db"
            acts = activity(path, manifest["focal_slot"]) if path.is_file() else None
            for metric, value in row["scores"].items():
                observations.setdefault(metric, []).append(
                    dict(
                        system=study["system_id"],
                        label=study["label"],
                        study=study["id"],
                        episode=manifest["run_id"],
                        case=list(case_key(row)),
                        value=value,
                        activity=acts,
                    )
                )
    weights = {
        metric: weight
        for entry in campaign["plan"]["domains"].values()
        for metric, weight in entry["metrics"].items()
    }
    report = []
    for metric, rows in sorted(observations.items()):
        by_system = {}
        for row in rows:
            by_system.setdefault(row["system"], []).append(row["value"])
        means = {system: fmean(values) for system, values in by_system.items()}
        floors = [means[s] for s in FLOORS if s in means]
        ceilings = [means[s] for s in CEILINGS if s in means]
        active = {s: v for s, v in means.items() if s not in FLOORS}
        flags = []
        if all(v >= 0.98 for v in means.values()):
            flags.append("saturated: every system scores the maximum")
        elif all(v <= 0.02 for v in means.values()):
            flags.append("dead: no system scores")
        elif max(means.values()) - min(means.values()) < 0.05:
            flags.append("society-driven: the candidate barely moves it")
        trivial = {s.split("/")[1]: means[s] for s in FLOORS if s in means}
        if floors and max(floors) >= 0.5:
            name = max(trivial, key=trivial.get)
            flags.append(f"free points: the {name} policy scores {100 * trivial[name]:.0f}")
        best = max(active.values(), default=0)
        for name, text in (("passive", "inaction pays: doing nothing"), ("random", "noise pays: acting at random")):
            if active and trivial.get(name, 0) > 0.02 and trivial[name] >= best - 0.02:
                flags.append(f"{text} matches the best active system")
        if floors and ceilings and max(floors) > max(ceilings) + 0.02:
            flags.append("inverted: a trivial policy beats the references")
        known = [r for r in rows if r["activity"]]
        for key, text in (("messages", "message volume"), ("effects", "number of actions")):
            r = correlation([x["activity"][key] for x in known], [x["value"] for x in known])
            if r is not None and r >= 0.6:
                flags.append(f"volume-driven: rises with the candidate's {text} (r = {r:.2f})")
        ordered = sorted(rows, key=lambda r: r["value"])
        report.append(
            dict(
                metric=metric,
                domain=metric.split(".")[0],
                weight=weights.get(metric),
                systems={by: round(100 * v, 2) for by, v in sorted(means.items())},
                floor=round(100 * min(floors), 2) if floors else None,
                ceiling=round(100 * max(ceilings), 2) if ceilings else None,
                spread=round(100 * (max(means.values()) - min(means.values())), 2),
                flags=flags,
                examples=dict(
                    lowest=[{k: r[k] for k in ("label", "study", "episode", "case", "value")} for r in ordered[:3]],
                    highest=[{k: r[k] for k in ("label", "study", "episode", "case", "value")} for r in ordered[-3:][::-1]],
                ),
            )
        )

    for domain in {row["domain"] for row in report}:
        members = [row for row in report if row["domain"] == domain]
        total = sum((row["weight"] or 0) * row["spread"] for row in members)
        for row in members:
            row["effective_weight"] = (
                round((row["weight"] or 0) * row["spread"] / total, 4) if total else None
            )
    return report
