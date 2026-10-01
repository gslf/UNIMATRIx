"""Preregister and independently verify the greedy/reciprocal domain tradeoff.

Runs full episodes on fresh seeds; never changes recipe weights or policy definitions.
python tests/tools/profile_holdout.py --output /path/outside/git --workers 4
"""

import argparse
import concurrent.futures
import json
import multiprocessing
from pathlib import Path

from audit_recipes import execute

from unimatrix.benchmark.fingerprints import runtime_fingerprint
from unimatrix.benchmark.recipes import RecipeRepository, bind_candidate, scoring_spec
from unimatrix.core.ids import digest
from unimatrix.evaluation.scoring import canonical_hash
from unimatrix.evaluation.stability import ranking_stability
from unimatrix.research.measurement_quality import audit_recipe, balanced_bank
from unimatrix.research.resolution import resolution_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 17))
    parser.add_argument("--descriptive", action="store_true", help="Collect descriptive control outcomes without a confirmation claim")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = RecipeRepository().get("standard-v1")
    source_digest = digest(source)

    recipe = balanced_bank(source, list(range(30000, 30008)), ticks=240, ident="profile-holdout-v2")
    recipe["bootstrap"] = dict(seed=1729, resamples=10000)
    coverage = audit_recipe(recipe)
    if not coverage["balanced_seed_blocks"] or not coverage["fully_crossed"]:
        raise ValueError("Complete balanced control design required")
    preflight = resolution_plan(recipe, systems=2)
    preflight_path = args.output / "design-preflight.json"
    if preflight_path.exists() and json.loads(preflight_path.read_text()) != preflight:
        raise ValueError("Frozen preflight differs; use another output directory")
    preflight_path.write_text(json.dumps(preflight, indent=2) + "\n")
    if not preflight["direction_confirmation_possible"] and not args.descriptive:
        raise SystemExit("Design cannot confirm even the maximum possible raw-score difference; "
                         "no episodes started. Inspect design-preflight.json and preregister a feasible design, "
                         "or select --descriptive for a descriptive control audit.")
    registration = dict(
        analysis_intent="descriptive" if args.descriptive else "confirmation",
        runtime=runtime_fingerprint(),
        source_recipe_digest=source_digest,
        coverage=coverage,
        recipe=recipe,
        systems=["greedy", "reciprocal"],
        hypotheses={"D2": "left_higher", "D5": "right_higher"},
        comparison="left=greedy; right=reciprocal; raw weighted attainment units",
        criterion="Both declared directions must have domain-family adjusted 95% intervals excluding zero; all eight domains are tested. No optional stopping or weight tuning.",
    )
    registration["digest"] = digest(registration)
    path = args.output / "preregistration.json"
    if path.exists() and json.loads(path.read_text()) != registration:
        raise ValueError("Frozen design/runtime differs; use another output directory")
    path.write_text(json.dumps(registration, indent=2) + "\n")
    output = args.output / "episodes.jsonl"
    rows = [json.loads(line) for line in output.read_text().splitlines()] if output.exists() else []
    done = {r["run_id"] for r in rows}
    if len(done) != len(rows):
        raise ValueError("Duplicate saved episodes")
    jobs = []
    for system in registration["systems"]:
        execution = bind_candidate(recipe, system)
        jobs.extend(
            (recipe["id"], system, c, m, str(args.output))
            for c, m in zip(recipe["cases"], execution["episodes"])
            if m["run_id"] not in done
        )
    print(
        f"Frozen {registration['digest']}; {len(rows)} recorded, {len(jobs)} remaining", flush=True
    )
    with (
        concurrent.futures.ProcessPoolExecutor(
            max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool,
        output.open("a") as stream,
    ):
        for row in pool.map(execute, jobs):
            rows.append(row)
            stream.write(json.dumps(row, allow_nan=False) + "\n")
            stream.flush()
            if len(rows) % 32 == 0:
                print(f"{len(rows)} episodes completed", flush=True)
    spec = scoring_spec(recipe)
    datasets = []
    for system in registration["systems"]:
        runs = [
            dict(
                r["case"],
                population=spec["population_by_seed"][str(r["case"]["seed"])],
                status=r["status"],
                scores={m: v["normalized_value"] for m, v in r["metrics"].items()},
            )
            for r in rows
            if r["system"] == system
        ]
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
    report = ranking_stability(datasets, spec)
    pair = report["comparisons"][0]
    observed = {r["domain"]: r["verdict"] for r in pair["domain_comparisons"]}
    confirmed = all(observed[d] == direction for d, direction in registration["hypotheses"].items())
    report.update(
        preregistration_digest=registration["digest"],
        runtime=registration["runtime"],
        declared_hypotheses_confirmed=confirmed and not args.descriptive,
        analysis_intent=registration["analysis_intent"],
        candidate_kind="scripted_controls",
    )
    (args.output / "profile-holdout.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"Hypotheses confirmed: {report['declared_hypotheses_confirmed']}. Report: {args.output / 'profile-holdout.json'}",
        flush=True,
    )
    if not confirmed and not args.descriptive:
        raise SystemExit("Holdout did not confirm the preregistered domain distinction")


if __name__ == "__main__":
    main()
