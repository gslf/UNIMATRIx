#!/usr/bin/env python3
"""USI scoring reference for already-normalized metrics. Python 3.11+, stdlib.

Not a simulator, not an extractor and not a certification of empirical validity.
The supplied examples are synthetic and the design manifest is a draft.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from statistics import fmean

ROOT = Path(__file__).resolve().parents[1]


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def read_json(path: Path) -> dict:
    with path.open(encoding="utf8") as f:
        value = json.load(f, parse_constant=lambda x: (_ for _ in ()).throw(
            ValueError(f"Non-finite JSON value: {x}")))
    if not isinstance(value, dict):
        raise ValueError("Top-level object required")
    return value


def expected_keys(manifest: dict) -> set[tuple]:
    return set(itertools.product(manifest["domains"], manifest["levels"],
                                 manifest["seeds"], manifest["roles"],
                                 manifest["replicates"]))


def validate(data: dict, manifest: dict) -> dict[tuple, float]:
    if data.get("suite_hash") != canonical_hash(manifest):
        raise ValueError("Suite hash mismatch")
    for field in ("benchmark_id", "scaffold_id"):
        if data.get(field) != manifest[field]:
            raise ValueError(f"Incompatible {field}")
    if data.get("data_kind") not in ("synthetic", "empirical"):
        raise ValueError("data_kind must identify synthetic or empirical input")
    if not isinstance(data.get("candidate_id"), str) or not data["candidate_id"]:
        raise ValueError("candidate_id required")
    if data.get("budget_track") not in ("accounted_compute", "opaque_compute"):
        raise ValueError("Unknown budget track")
    expected = expected_keys(manifest)
    if not isinstance(data.get("runs"), list):
        raise ValueError("runs must be a list")
    for d, spec in manifest["domains"].items():
        weights = spec["metrics"]
        if len(weights) != 3 or not math.isclose(sum(weights.values()), 1):
            raise ValueError(f"Invalid metric weights: {d}")
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
               for v in weights.values()):
            raise ValueError(f"Non-positive weights: {d}")
    values = {}
    fields = ("domain", "level", "seed", "role", "replicate")
    for row in data["runs"]:
        if not isinstance(row, dict) or any(k not in row for k in fields):
            raise ValueError("Malformed run")
        if any(type(row[k]) is not int for k in ("level", "seed", "replicate")):
            raise ValueError("Integer level, seed and replicate required (not bool)")
        key = tuple(row[k] for k in fields)
        if key not in expected:
            raise ValueError(f"Unexpected cell: {key}")
        if key in values:
            raise ValueError(f"Duplicate cell: {key}")
        if row.get("status") != "completed":
            raise ValueError(f"Unfinished cell: {key}; never impute infrastructure failures")
        population = manifest["population_by_seed"][str(row["seed"])]
        if row.get("population") != population:
            raise ValueError(f"Wrong population: {key}")
        weights = manifest["domains"][row["domain"]]["metrics"]
        scores = row.get("scores", {})
        if not isinstance(scores, dict) or set(scores) != set(weights):
            raise ValueError(f"Wrong metric catalog: {key}")
        for name, value in scores.items():
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not 0 <= value <= 1):
                raise ValueError(f"Invalid normalized value: {name}={value}")
        values[key] = sum(weights[k] * scores[k] for k in weights)
    missing = expected - set(values)
    if missing:
        raise ValueError(f"Missing {len(missing)} cells; cannot compute a complete-suite score")
    return values


def domain_means(values: dict[tuple, float], manifest: dict) -> dict[str, float]:
    # Explicit equal weights by level; the supplied manifest is fully balanced.
    out = {}
    for domain in manifest["domains"]:
        out[domain] = fmean(fmean(v for k, v in values.items()
                                  if k[0] == domain and k[1] == level)
                            for level in manifest["levels"])
    return out


def clustered(values: dict[tuple, float], manifest: dict) -> dict[tuple, dict[int, float]]:
    temporary = defaultdict(lambda: defaultdict(list))
    for (domain, level, seed, role, replicate), score in values.items():
        population = manifest["population_by_seed"][str(seed)]
        temporary[(domain, population)][seed].append(score)
    return {stratum: {seed: fmean(scores) for seed, scores in seeds.items()}
            for stratum, seeds in temporary.items()}


def percentile(xs: list[float], p: float) -> float:
    ys = sorted(xs)
    position = (len(ys) - 1) * p
    lo, hi = math.floor(position), math.ceil(position)
    return ys[lo] + (ys[hi] - ys[lo]) * (position - lo)


def interval(xs: list[float]) -> list[float]:
    return [round(100 * percentile(xs, p), 6) for p in (.025, .975)]


def bootstrap(left: dict[tuple, float], manifest: dict,
              right: dict[tuple, float] | None = None) -> tuple[list[float], dict[str, list[float]]]:
    """Stratified seed-cluster resampling; paired same draws when right is given.

    Seed clusters retain levels, roles and inference repeats. Scenario families
    are fixed; the interval does not generalize to unseen domain definitions.
    """
    l_clusters = clustered(left, manifest)
    r_clusters = clustered(right, manifest) if right is not None else None
    rng = random.Random(manifest["bootstrap"]["seed"])
    total_samples = []
    by_domain = {d: [] for d in manifest["domains"]}
    for _ in range(manifest["bootstrap"]["resamples"]):
        one_draw = defaultdict(list)
        for stratum in sorted(l_clusters):
            seeds = sorted(l_clusters[stratum])
            sampled = rng.choices(seeds, k=len(seeds))
            value = fmean(l_clusters[stratum][s] - (
                r_clusters[stratum][s] if r_clusters is not None else 0)
                for s in sampled)
            one_draw[stratum[0]].append((value, len(seeds)))
        domain_draw = {}
        for d, rows in one_draw.items():
            # Preserve each stratum's design weight (equal pools in Core).
            domain_draw[d] = sum(v*n for v, n in rows) / sum(n for _, n in rows)
            by_domain[d].append(domain_draw[d])
        total_samples.append(fmean(domain_draw.values()))
    return total_samples, by_domain


def summarize(data: dict, manifest: dict) -> dict:
    values = validate(data, manifest)
    means = domain_means(values, manifest)
    samples, domain_samples = bootstrap(values, manifest)
    return {
        "candidate_id": data["candidate_id"],
        "data_kind": data["data_kind"],
        "certified": False,
        "release_status": manifest["release_status"],
        "benchmark_id": manifest["benchmark_id"],
        "suite_hash": canonical_hash(manifest),
        "scaffold_id": data["scaffold_id"],
        "budget_track": data["budget_track"],
        "completed_episodes": len(values),
        "ticks_per_episode": manifest["ticks"],
        "usi": round(100*fmean(means.values()), 6),
        "ci95": interval(samples),
        "domains": {d: {"score": round(100*v, 6), "ci95": interval(domain_samples[d])}
                    for d, v in means.items()},
        "bootstrap_resamples": manifest["bootstrap"]["resamples"],
        "interval_scope": "Sampled seed clusters within fixed domains and populations; not construct validity.",
        "warning": "Scoring reference only. Synthetic examples are not real model evaluations. This draft cannot certify a release.",
    }


def compare(left: dict, right: dict, manifest: dict) -> dict:
    for field in ("data_kind", "benchmark_id", "suite_hash", "scaffold_id", "budget_track"):
        if left.get(field) != right.get(field):
            raise ValueError(f"Cannot compare mismatched {field}")
    a, b = validate(left, manifest), validate(right, manifest)
    samples, _ = bootstrap(a, manifest, b)
    ma, mb = domain_means(a, manifest), domain_means(b, manifest)
    return {"left": left["candidate_id"], "right": right["candidate_id"],
            "data_kind": left["data_kind"], "certified": False,
            "paired_delta": round(100*fmean(ma[d]-mb[d] for d in ma), 6),
            "paired_ci95": interval(samples), "matched_episodes": len(a),
            "warning": "A paired difference under the supplied design; not evidence of general intelligence."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--manifest", type=Path, default=ROOT/"examples/benchmark-core.json")
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        manifest, data = read_json(args.manifest), read_json(args.input)
        result = compare(data, read_json(args.compare), manifest) if args.compare else summarize(data, manifest)
    except (ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    rendered = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
