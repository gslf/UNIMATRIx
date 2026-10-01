"""Per-case floor and anchor scores from scripted reference policies, cached per revision.

The floor is the weakest trivial policy (random or passive), the strong anchor the best
scripted reference (the oracle, the leading coordinator or the reciprocal peer); neither anchor bounds candidate performance. All are
deterministic and cost no provider calls, so every candidate of a recipe revision shares them.
"""

from pathlib import Path

from ..benchmark.parallel import execute_episodes
from ..benchmark.recipes import bind_candidate, scoring_spec
from ..benchmark.scheduler import collect, run_episode_offloaded
from ..core.ids import digest
from ..persistence.json_files import read_json, write_json
from .scoring import validate

FLOORS = ("random", "passive")
CEILINGS = ("oracle", "coordinator", "reciprocal")


def reference_folder(runs_dir, execution):
    return Path(runs_dir) / "references" / digest(execution["suite"])


async def ensure_references(spec, runs_dir, execution, save=lambda: None, parallelism=1):
    """Return {case_key: (floor, ceiling)} for the recipe, computing missing episodes."""
    if scoring_spec(spec) != execution["suite"]:
        raise ValueError("reference_suite_mismatch")
    folder = reference_folder(runs_dir, execution)
    result = {}
    for system in FLOORS + CEILINGS:
        bound = bind_candidate(spec, system)
        record_path = folder / system / "reference.json"
        record = read_json(record_path) if record_path.is_file() else dict(completed_episodes=0)
        pending = [
            m
            for m in bound["episodes"]
            if m["run_id"] not in set(record.get("completed_episode_ids", []))
        ]
        if pending:

            async def worker(manifest):
                outcome = await run_episode_offloaded(manifest, folder / system / "episodes")
                if outcome["status"] != "completed":
                    raise ValueError("Reference episode did not complete")
                return outcome["diagnostics"]

            def persist():
                write_json(record_path, record)
                save()

            await execute_episodes(
                record, bound["episodes"], worker, persist, parallelism, "telemetry", []
            )
            write_json(record_path, record)
        data = collect(bound, folder / system / "episodes")
        result[system] = validate(data, bound["suite"])
    keys = set.intersection(*(set(result[s]) for s in FLOORS + CEILINGS))
    return {
        key: (min(result[s][key] for s in FLOORS), max(result[s][key] for s in CEILINGS))
        for key in keys
    }
