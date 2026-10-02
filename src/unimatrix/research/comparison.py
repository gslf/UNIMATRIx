"""Smoke-to-model comparisons on shared cases under identical conditions."""

from collections import defaultdict
from statistics import fmean

from ..core.ids import digest
from ..evaluation.scoring import case_key, validate
from ..evaluation.uncertainty import METHOD, seed_interval
from ..persistence.json_files import read_json


def compatible(source, target):
    """Metadata and sample size may differ; mechanics, peers and weights may not."""
    ignored = {"id", "name", "description", "cases", "bootstrap"}
    def conditions(record):
        return {k: v for k, v in record["plan"].items() if k not in ignored}
    return (bool(source.get("runtime")) and bool(source.get("research_runtime"))
            and source.get("mode") == "smoke"
            and source.get("split") == target.get("split")
            and source.get("runtime") == target.get("runtime")
            and source.get("research_runtime") == target.get("research_runtime")
            and conditions(source) == conditions(target))


def weighted_mean(rows):
    domains = defaultdict(lambda: defaultdict(list))
    for key, value in rows.items():
        domains[key[0]][key[1]].append(value)
    return fmean(fmean(fmean(v) for v in strata.values()) for strata in domains.values())


def paired_summary(left, right, resamples=1000, seed=0):
    keys = sorted(left.keys() & right.keys())
    if not keys:
        raise ValueError("No shared cases")
    left, right = {k: left[k] for k in keys}, {k: right[k] for k in keys}
    delta = {k: left[k] - right[k] for k in keys}
    seeds = sorted({k[2] for k in keys})
    strata = {(k[0], k[1]) for k in keys}

    enough = all(len({k[2] for k in keys if k[:2] == s}) >= 2 for s in strata)
    ci = None
    if enough:
        ci = [100*v for v in seed_interval(delta, paired=True)]
    return dict(
        interval_method=METHOD,
        cases=len(keys), seeds=len(seeds), model_score=100 * weighted_mean(left),
        baseline_score=100 * weighted_mean(right), delta=100 * weighted_mean(delta), ci95=ci,
        wins=sum(v > 1e-9 for v in delta.values()), ties=sum(abs(v) <= 1e-9 for v in delta.values()),
        losses=sum(v < -1e-9 for v in delta.values()),
        verdict="descriptive" if ci is None else "higher" if ci[0] > 0 else "lower" if ci[1] < 0 else "inconclusive",
        domains={d: 100 * weighted_mean({k: v for k, v in delta.items() if k[0] == d})
                 for d in sorted({k[0] for k in keys})},
    )


def compare_smoke(source, target, directory):
    if not compatible(source, target):
        raise ValueError("Smoke and evaluation must share runtime, split, peers, rules and metric weights")
    references = [s for s in source["studies"] if s["status"] == "completed"
                  and s["intervention"] is None and s["system_id"].startswith("baseline/")]
    models = [s for s in target["studies"] if s["status"] == "completed"
              and s["intervention"] is None and not s["system_id"].startswith("baseline/")]

    def load(record, study):
        data = read_json(directory / record["id"] / "studies" / study["id"] / "results.json")
        spec = study["execution"]["suite"]
        values = validate(data, spec)

        cases = {case_key(c): digest(c) for c in spec["cases"]}
        return values, cases

    rows = []
    for model in models:
        left, actual_left = load(target, model)
        for baseline in references:
            right, actual_right = load(source, baseline)
            keys = {k for k in left.keys() & right.keys() if actual_left[k] == actual_right[k]}
            if not keys:
                continue
            summary = paired_summary({k: left[k] for k in keys}, {k: right[k] for k in keys})
            rows.append(dict(model=model["label"], model_id=model["system_id"],
                             baseline=baseline["label"], baseline_id=baseline["system_id"],
                             model_cases=len(left), smoke_cases=len(right), **summary))
    return dict(source_id=source["id"], source_name=source["name"], target_id=target["id"],
                target_mode=target.get("mode", "unknown"), rows=rows,
                note="Only identical shared cases are compared. Unmatched cases are excluded. "
                     "One seed per stratum gives descriptive differences only. Oracle uses full state.")
