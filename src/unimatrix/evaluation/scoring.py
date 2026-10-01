"""Validate and score completed benchmark results."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean

from .uncertainty import METHOD, seed_estimator, seed_interval


def case_key(row) -> tuple:
    from ..scenarios.layers import layers_key

    return (row["domain"], layers_key(row["layers"]), row["seed"], row["role"], row["replicate"])


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def read_json(path: Path) -> dict:
    with path.open(encoding="utf8") as f:
        value = json.load(
            f,
            parse_constant=lambda x: (_ for _ in ()).throw(
                ValueError(f"Non-finite JSON value: {x}")
            ),
        )
    if not isinstance(value, dict):
        raise ValueError("Top-level object required")
    return value


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
    expected = {case_key(c) for c in manifest["cases"]}
    if not isinstance(data.get("runs"), list):
        raise ValueError("runs must be a list")
    for d, spec in manifest["domains"].items():
        weights = spec["metrics"]
        if len(weights) != 3 or not math.isclose(sum(weights.values()), 1):
            raise ValueError(f"Invalid metric weights: {d}")
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in weights.values()
        ):
            raise ValueError(f"Non-positive weights: {d}")
    values = {}
    fields = ("domain", "layers", "seed", "role", "replicate")
    for row in data["runs"]:
        if not isinstance(row, dict) or any(k not in row for k in fields):
            raise ValueError("Malformed run")
        if any(type(row[k]) is not int for k in ("seed", "replicate")):
            raise ValueError("Integer seed and replicate required (not bool)")
        key = case_key(row)
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
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"Invalid normalized value: {name}={value}")
        values[key] = sum(weights[k] * scores[k] for k in weights)
    missing = expected - set(values)
    if missing:
        raise ValueError(f"Missing {len(missing)} cells; cannot compute a complete-suite score")
    return values


def domain_means(values: dict[tuple, float], manifest: dict) -> dict[str, float]:

    out = {}
    for domain in manifest["domains"]:
        out[domain] = fmean(
            fmean(v for k, v in values.items() if k[0] == domain and k[1] == label)
            for label in sorted({k[1] for k in values if k[0] == domain})
        )
    return out


def percentile(xs: list[float], p: float) -> float:
    ys = sorted(xs)
    position = (len(ys) - 1) * p
    lo, hi = math.floor(position), math.ceil(position)
    return ys[lo] + (ys[hi] - ys[lo]) * (position - lo)


def restrict(data: dict, manifest: dict, keys: set) -> tuple[dict, dict]:
    """The same results and suite limited to the given case keys, with a fresh suite hash."""
    spec = dict(manifest, cases=[c for c in manifest["cases"] if case_key(c) in keys])
    rows = [r for r in data["runs"] if case_key(r) in keys]
    return dict(data, runs=rows, suite_hash=canonical_hash(spec)), spec


def common_cases(left: dict, left_spec: dict, right: dict, right_spec: dict):
    """Both results restricted to the cases they share, for a paired comparison."""
    keys = {case_key(c) for c in left_spec["cases"]} & {case_key(c) for c in right_spec["cases"]}
    if not keys:
        raise ValueError("No shared cases to compare")
    a, spec = restrict(left, left_spec, keys)
    b, _ = restrict(right, right_spec, keys)
    return a, b, spec


def normalized_scores(values: dict[tuple, float], references: dict) -> dict:
    """Signed per-case reference gain; the strong reference is an anchor, not a cap."""
    from .stats import normalize

    result = {}
    for key, score in values.items():
        bounds = references.get(key)
        result[key] = None if bounds is None else normalize(score, *bounds)
    return result


def summarize(data: dict, manifest: dict, references: dict | None = None) -> dict:
    from .stats import iqm, optimality_gap

    values = validate(data, manifest)
    means = domain_means(values, manifest)
    robust = dict(
        iqm=round(100 * iqm(list(values.values())), 6),
        optimality_gap=round(100 * optimality_gap(list(values.values())), 6),
    )
    if references is not None:
        scored = normalized_scores(values, references)
        usable = {k: v for k, v in scored.items() if v is not None}
        complete = len(usable) == len(values)
        rating = fmean(domain_means(usable, manifest).values()) if complete else None
        robust.update(
            scoring_version="reference-gain-v1",
            rating=rating,
            rating_ci95=seed_interval(usable, references=references, comparisons=len(means) + 1)
            if complete
            else None,
            rating_domains={
                d: dict(score=v, ci95=seed_interval(usable, references=references, domains=[d], comparisons=len(means) + 1))
                for d, v in domain_means(usable, manifest).items()
            }
            if complete
            else {},
            rating_iqm=iqm(list(usable.values())) if complete else None,
            normalized_cells=len(usable),
            degenerate_cells=len(scored) - len(usable),
        )
    return {
        **robust,
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
        "usi": round(100 * fmean(means.values()), 6),
        "ci95": [100*v for v in seed_interval(values, comparisons=len(means) + 1)],
        "domains": {
            d: {"score": round(100 * v, 6), "ci95": [100*x for x in seed_interval(values, domains=[d], comparisons=len(means) + 1)]}
            for d, v in means.items()
        },
        "strata": {
            "population": {
                pool: round(
                    100
                    * fmean(
                        value
                        for key, value in values.items()
                        if manifest["population_by_seed"][str(key[2])] == pool
                    ),
                    6,
                )
                for pool in sorted(set(manifest["population_by_seed"].values()))
            },
            "role": {
                role: round(
                    100 * fmean(value for key, value in values.items() if key[3] == role), 6
                )
                for role in sorted({k[3] for k in values})
            },
            "complexity": {
                label: round(
                    100 * fmean(value for key, value in values.items() if key[1] == label), 6
                )
                for label in sorted({k[1] for k in values})
            },
        },
        "bootstrap_resamples": manifest["bootstrap"]["resamples"],
        "interval_method": METHOD,
        "interval_scope": "Independent seed clusters; simultaneous aggregate and domains; fixed tasks, peers and budgets.",
        "warning": (
            "Scoring reference only. Synthetic examples are not real model evaluations. "
            "Draft suites cannot certify a release."
            if data["data_kind"] == "synthetic"
            else "Results describe this frozen recipe and candidate configuration; "
                 "interpret intervals using the stated design and independent seed count."
        ),
    }


def compare(left: dict, right: dict, manifest: dict, *, comparisons=1) -> dict:
    for field in ("data_kind", "benchmark_id", "suite_hash", "scaffold_id", "budget_track"):
        if left.get(field) != right.get(field):
            raise ValueError(f"Cannot compare mismatched {field}")
    from .stats import paired, probability_of_improvement

    a, b = validate(left, manifest), validate(right, manifest)
    ma, mb = domain_means(a, manifest), domain_means(b, manifest)
    keys = sorted(a)
    av, weights = seed_estimator(a)
    bv, _ = seed_estimator(b)
    matched = paired(list(av.values()), [bv[s] for s in av])
    equal_weights = len({round(w, 12) for w in weights.values()}) == 1
    cluster_se = matched["se"] if equal_weights else None
    return {
        "paired_se": None if cluster_se is None else round(100 * cluster_se, 6),
        "matched_seed_clusters": len(av),
        "seed_rho": matched["rho"],
        "effective_pairs": matched["effective_pairs"],
        "probability_of_improvement": probability_of_improvement(
            [a[k] for k in keys], [b[k] for k in keys]
        ),
        "left": left["candidate_id"],
        "right": right["candidate_id"],
        "data_kind": left["data_kind"],
        "certified": False,
        "paired_delta": round(100 * fmean(ma[d] - mb[d] for d in ma), 6),
        "paired_ci95": [100*v for v in seed_interval({k: a[k] - b[k] for k in a}, paired=True, comparisons=comparisons)],
        "interval_method": METHOD,
        "matched_episodes": len(a),
        "warning": "A paired difference under the supplied design; not evidence of general intelligence.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        manifest, data = read_json(args.manifest), read_json(args.input)
        result = (
            compare(data, read_json(args.compare), manifest)
            if args.compare
            else summarize(data, manifest)
        )
    except (ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    rendered = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
