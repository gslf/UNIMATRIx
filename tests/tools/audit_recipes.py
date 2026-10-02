"""Run bundled recipes through the production runner with resumable, private audit output.

Usage: python tests/tools/audit_recipes.py --output /tmp/recipe-audit --workers 8
Only summaries are retained; each episode's temporary SQLite evidence is verified
by the runner before deletion. Use --model-config for real inference with retained private evidence.
"""

import argparse
import asyncio
import concurrent.futures
import json
import math
import multiprocessing
import tempfile
from pathlib import Path
from statistics import fmean

from unimatrix.benchmark.duration import BudgetClock, recover, remaining
from unimatrix.benchmark.fingerprints import runtime_fingerprint
from unimatrix.benchmark.personas import apply_profile
from unimatrix.benchmark.recipes import RecipeRepository, bind_candidate
from unimatrix.benchmark.scheduler import run_episode
from unimatrix.core.ids import digest
from unimatrix.evaluation.scoring import case_key
from unimatrix.persistence.event_store import EventStore
from unimatrix.persistence.json_files import read_json, write_json
from unimatrix.persistence.lease import episode_lease
from unimatrix.persistence.replay import replay

SYSTEMS = ("passive", "random", "independent", "greedy", "reciprocal", "coordinator", "oracle")


async def audit_model(args, specs):
    """Real provider calls, immutable identities and retained per-decision evidence."""
    config = json.loads(args.model_config.read_text())
    jobs = []
    for name, spec in specs.items():
        for profile in args.profiles or [None]:
            candidate = (
                apply_profile(config, spec.get("candidate_profiles", {}), profile)
                if profile
                else config
            )
            seen = set()
            for case, manifest in zip(spec["cases"], bind_candidate(spec, candidate)["episodes"]):
                if args.domains and case["domain"] not in args.domains:
                    continue
                key = case_key(case)
                stratum = (key[0], key[1], key[3])
                if args.cell_panel and stratum in seen:
                    continue
                seen.add(stratum)
                jobs.append((name, profile, case, manifest))
    identity = dict(
        runtime=runtime_fingerprint(),
        recipes={k: digest(v) for k, v in specs.items()},
        candidate=digest(config),
        profiles=args.profiles,
        cell_panel=args.cell_panel,
        runs=[m["run_id"] for _, _, _, m in jobs],
    )
    frozen = json.dumps(identity, sort_keys=True, indent=2) + "\n"
    metadata = args.output / "model-identity.json"
    if metadata.exists() and metadata.read_text() != frozen:
        raise ValueError("Output belongs to another model, runtime or case selection")
    metadata.write_text(frozen)
    output = args.output / "model-episodes.jsonl"
    done = (
        {json.loads(line)["run_id"] for line in output.read_text().splitlines()}
        if output.exists()
        else set()
    )
    semaphore = asyncio.Semaphore(args.workers)

    async def one(name, profile, case, manifest):
        if manifest["run_id"] in done:
            return
        async with semaphore:
            result = await run_episode(manifest, args.output / "evidence")
            store = EventStore(args.output / "evidence" / manifest["run_id"] / "episode.db", read_only=True)
            try:
                result["verification"] = replay(store)
            finally:
                store.close()
            if result["status"] != "completed" or result["verification"]["completed_tick"] != manifest["ticks"]:
                raise RuntimeError(f"Incomplete model episode: {manifest['run_id']}")
            for metric in result["metrics"].values():
                value = metric["normalized_value"]
                if (
                    not math.isfinite(value)
                    or not 0 <= value <= 1
                    or not metric["evidence_event_ids"]
                ):
                    raise RuntimeError(f"Invalid model metric: {manifest['run_id']}")
            row = dict(
                recipe=name,
                profile=profile,
                case=case,
                run_id=manifest["run_id"],
                status=result["status"],
                verification=result["verification"],
                metrics=result["metrics"],
                diagnostics=result["diagnostics"],
            )
            with output.open("a") as stream:
                stream.write(json.dumps(row, allow_nan=False) + "\n")
            done.add(manifest["run_id"])
            print(f"{len(done)}/{len(jobs)} real-model episodes completed", flush=True)



    for name, profile in dict.fromkeys((n, p) for n, p, _, _ in jobs):
        path = args.output / ("budget-" + digest([name, profile])[:16] + ".json")
        record = read_json(path) if path.exists() else dict(frozen_recipe=specs[name], wall_seconds=0.0)
        if record["frozen_recipe"] != specs[name]:
            raise ValueError("audit_budget_recipe_mismatch")
        recover(record)
        write_json(path, record)
        pending = [job for job in jobs if job[:2] == (name, profile) and job[3]["run_id"] not in done]
        if not pending:
            continue
        if not remaining(record):
            raise ValueError("recipe_wall_time_budget_exhausted")
        clock = BudgetClock(record)
        write_json(path, record)
        tasks = []
        try:
            async with asyncio.timeout(remaining(record)):
                tasks = [asyncio.create_task(one(*job)) for job in pending]
                await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            clock.close()
            write_json(path, record)
    print("Real-model panel complete; model-identity.json records its exact scope.", flush=True)


def execute(job):
    recipe, system, case, manifest, directory = job
    with tempfile.TemporaryDirectory(prefix="episode-", dir=directory) as scratch:
        result = asyncio.run(run_episode(manifest, scratch))
        store = EventStore(Path(scratch) / manifest["run_id"] / "episode.db", read_only=True)
        try:
            result["verification"] = replay(store)
        finally:
            store.close()
    if result["status"] != "completed" or result["verification"]["completed_tick"] != manifest["ticks"]:
        raise RuntimeError(f"Incomplete audit episode: {manifest['run_id']}")
    for metric in result["metrics"].values():
        value = metric["normalized_value"]
        if not math.isfinite(value) or not 0 <= value <= 1 or not metric["evidence_event_ids"]:
            raise RuntimeError(f"Invalid metric or missing evidence: {manifest['run_id']}")
    return dict(
        recipe=recipe,
        system=system,
        case=case,
        run_id=manifest["run_id"],
        status=result["status"],
        verification=result["verification"],
        metrics=result["metrics"],
        diagnostics=result["diagnostics"],
    )


def summarize(output, specs):
    """Fail a complete audit when a bundled case has no usable reference range."""
    scores = {recipe: {system: {} for system in SYSTEMS} for recipe in specs}
    for line in output.read_text().splitlines():
        row = json.loads(line)
        spec = specs[row["recipe"]]
        key = case_key(row["case"])
        target = scores[row["recipe"]][row["system"]]
        if key in target:
            raise RuntimeError("Duplicate case in audit output")
        weights = spec["domains"][row["case"]["domain"]]["metrics"]
        target[key] = sum(w * row["metrics"][m]["normalized_value"] for m, w in weights.items())
    if any(
        set(values) != {case_key(c) for c in specs[recipe]["cases"]}
        for recipe, systems in scores.items()
        for values in systems.values()
    ):
        print(
            "Partial audit retained; resume without --domains for the complete check.", flush=True
        )
        return
    report = {}
    for recipe, systems in scores.items():
        degenerate = [
            key
            for key in systems["passive"]
            if max(systems[s][key] for s in ("oracle", "coordinator", "reciprocal"))
            - min(systems[s][key] for s in ("passive", "random"))
            < 0.05
        ]
        report[recipe] = dict(
            cases=len(specs[recipe]["cases"]),
            degenerate_cases=degenerate,
            systems={
                system: dict(
                    score=100 * fmean(values.values()),
                    domains={
                        domain: 100 * fmean(v for k, v in values.items() if k[0] == domain)
                        for domain in specs[recipe]["domains"]
                    },
                )
                for system, values in systems.items()
            },
        )
    (output.parent / "audit-summary.json").write_text(json.dumps(report, indent=2) + "\n")
    if any(r["degenerate_cases"] for r in report.values()):
        raise RuntimeError("Degenerate cases found; inspect audit-summary.json")
    print("Complete audit: no degenerate reference ranges. See audit-summary.json.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--recipes",
        nargs="+",
        choices=("standard-v1", "validation-v1"),
        default=["standard-v1", "validation-v1"],
    )
    parser.add_argument("--domains", nargs="+", choices=[f"D{i}" for i in range(1, 9)])
    parser.add_argument(
        "--model-config", type=Path, help="Local candidate JSON; retains private evidence"
    )
    parser.add_argument("--profiles", nargs="+", help="Recipe candidate profile IDs")
    parser.add_argument(
        "--cell-panel",
        action="store_true",
        help="One full episode per domain, condition and role; not a full recipe",
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be between 1 and 16")
    args.output.mkdir(parents=True, exist_ok=True)
    specs = {name: RecipeRepository().get(name) for name in args.recipes}
    if args.model_config:
        with episode_lease(args.output / "model-audit.lock"):
            asyncio.run(audit_model(args, specs))
        return
    if args.profiles or args.cell_panel:
        parser.error("--profiles and --cell-panel require --model-config")
    identity = dict(
        runtime=runtime_fingerprint(),
        recipes={k: digest(v) for k, v in specs.items()},
        systems=SYSTEMS,
    )
    metadata = args.output / "identity.json"
    frozen = json.dumps(identity, sort_keys=True, indent=2) + "\n"
    if metadata.exists() and metadata.read_text() != frozen:
        parser.error(
            "Output belongs to a different runtime or recipe revision; use a new directory"
        )
    metadata.write_text(frozen)
    output = args.output / "episodes.jsonl"
    done = (
        {json.loads(line)["run_id"] for line in output.read_text().splitlines()}
        if output.exists()
        else set()
    )
    jobs = []
    for name, spec in specs.items():
        for system in SYSTEMS:
            execution = bind_candidate(spec, system)
            jobs.extend(
                (name, system, case, manifest, str(args.output))
                for case, manifest in zip(spec["cases"], execution["episodes"])
                if manifest["run_id"] not in done
                and (not args.domains or manifest["domain"] in args.domains)
            )
    print(f"{len(done)} recorded; {len(jobs)} episodes remaining", flush=True)
    with (
        concurrent.futures.ProcessPoolExecutor(
            max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool,
        output.open("a") as stream,
    ):
        futures = [pool.submit(execute, job) for job in jobs]
        for count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                result = future.result()
            except BaseException:
                for pending in futures:
                    pending.cancel()
                raise
            stream.write(json.dumps(result, allow_nan=False) + "\n")
            stream.flush()
            if count % 32 == 0 or count == len(jobs):
                print(f"{count}/{len(jobs)} completed", flush=True)

    summarize(output, specs)


if __name__ == "__main__":
    main()
