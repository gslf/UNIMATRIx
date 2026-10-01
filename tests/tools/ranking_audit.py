"""Recompute matched ranking stability from audit_recipes.py evidence (no provider calls).

python tests/tools/ranking_audit.py /tmp/recipe-audit
"""

import argparse
import gzip
import json
from pathlib import Path

from unimatrix.benchmark.fingerprints import runtime_fingerprint
from unimatrix.benchmark.recipes import RecipeRepository, scoring_spec
from unimatrix.core.ids import digest
from unimatrix.evaluation.scoring import canonical_hash, case_key
from unimatrix.evaluation.stability import ranking_stability
from unimatrix.evaluation.stats import spearman


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--output", type=Path, help="Write analysis separately from archived reports"
    )
    parser.add_argument(
        "--reanalyse",
        action="store_true",
        help="Analyse archived metric values under current statistics; preserve source runtime and original report",
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="Analyse bounded raw metric scores without per-case reference-gain scaling",
    )
    args = parser.parse_args()
    identity = json.loads((args.directory / "identity.json").read_text())
    if identity["runtime"] != runtime_fingerprint() and not args.reanalyse:
        raise ValueError("Audit runtime differs; do not combine revisions")
    specs = {name: RecipeRepository().get(name) for name in identity["recipes"]}
    if any(digest(v) != identity["recipes"][k] for k, v in specs.items()):
        raise ValueError("Recipe digest differs")
    panels = {name: {} for name in specs}
    source = args.directory / "episodes.jsonl"
    text = (
        source.read_text()
        if source.exists()
        else gzip.decompress((args.directory / "episodes.jsonl.gz").read_bytes()).decode()
    )
    for line in text.splitlines():
        r = json.loads(line)
        if r["status"] != "completed" or r["verification"]["completed_tick"] != specs[r["recipe"]]["ticks"]:
            raise ValueError("Incomplete episode")
        panels[r["recipe"]].setdefault(r["system"], []).append(r)
    report = dict(
        runtime=identity["runtime"],
        analysis_runtime=runtime_fingerprint(),
        archived_metrics=args.reanalyse,
        recipes={},
    )
    if args.raw:
        report["analysis_mode"] = "raw-metric-v1"
    for name, systems in panels.items():
        spec = scoring_spec(specs[name])
        spec["runtime_fingerprint"] = identity["runtime"]
        datasets = []
        scores = {}
        for system, rows in systems.items():
            runs = [
                dict(
                    r["case"],
                    status="completed",
                    population=spec["population_by_seed"][str(r["case"]["seed"])],
                    scores={m: v["normalized_value"] for m, v in r["metrics"].items()},
                )
                for r in rows
            ]
            scores[system] = {
                case_key(r): sum(
                    v * spec["domains"][r["domain"]]["metrics"][m] for m, v in r["scores"].items()
                )
                for r in runs
            }
            datasets.append(
                dict(
                    candidate_id=system,
                    data_kind="empirical",
                    runs=runs,
                    suite_hash=canonical_hash(spec),
                    scaffold_id=spec["scaffold_id"],
                    benchmark_id=spec["benchmark_id"],
                    budget_track="accounted_compute",
                )
            )
        references = {
            k: (
                min(scores[s][k] for s in ("passive", "random")),
                max(scores[s][k] for s in ("oracle", "coordinator", "reciprocal")),
            )
            for k in scores["passive"]
        }
        result = ranking_stability(datasets, spec, None if args.raw else references)
        result["candidate_kind"] = "scripted_controls"
        result["recipe_digest"] = identity["recipes"][name]
        report["recipes"][name] = result
    if len(report["recipes"]) == 2:
        left, right = report["recipes"].values()
        a = {r["system"]: r["score"] for r in left["ranking"]}
        b = {r["system"]: r["score"] for r in right["ranking"]}
        names = sorted(a.keys() & b.keys())
        report["cross_recipe_spearman"] = spearman([a[n] for n in names], [b[n] for n in names])
        report["cross_recipe_scope"] = (
            "Disjoint seeds and different recipes; replication, not identical-design holdout"
        )
    path = args.output or args.directory / (
        "ranking-raw.json" if args.raw else
        "ranking-reanalysis.json" if args.reanalyse else "ranking-stability.json"
    )
    path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(path)


if __name__ == "__main__":
    main()
