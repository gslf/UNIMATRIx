"""Persistent benchmark runs and leaderboards over immutable, named plans."""

import asyncio
import re
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from ..core.ids import digest
from ..evaluation.scoring import summarize
from ..persistence.event_store import EventStore
from ..persistence.json_files import read_json, write_json
from ..persistence.lease import episode_lease
from .fingerprints import runtime_fingerprint
from .parallel import execute_episodes, validate_parallelism
from .plans import bind_candidate
from .scheduler import collect, run_episode


def now():
    return datetime.now(timezone.utc).isoformat()


class BenchmarkService:
    def __init__(self, directory, plans):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.plans = plans
        self.active = {}

    def folder(self, ident):
        if not re.fullmatch(r"[a-f0-9]{24}", ident):
            raise ValueError("Unknown benchmark run")
        return self.directory / ident

    def read(self, ident):
        return read_json(self.folder(ident) / "benchmark.json")

    def save(self, run):
        write_json(self.folder(run["id"]) / "benchmark.json", run)

    def summary(self, run):
        result = {k: v for k, v in run.items() if k not in {"execution", "candidate"}}
        if run["status"] == "running" and run["id"] not in self.active:
            result["status"] = "interrupted"
        completed_ticks = run["completed_episodes"] * 240
        current = run.get(
            "current_episodes", [run["current_episode"]] if run.get("current_episode") else []
        )
        for ident in current if run["status"] != "completed" else []:
            db = self.folder(run["id"]) / "episodes" / ident / "episode.db"
            if db.is_file():
                store = EventStore(db, read_only=True)
                try:
                    completed_ticks += store.status()["completed_tick"]
                finally:
                    store.close()
        result["completed_ticks"] = completed_ticks
        result["total_ticks"] = run["total_episodes"] * 240
        return result

    def runs(self):
        return [
            self.summary(read_json(path))
            for path in sorted(self.directory.glob("*/benchmark.json"), reverse=True)
        ]

    async def start(self, candidate, model_id, spec=None, parallelism=1):
        validate_parallelism(parallelism)
        if self.active:
            raise ValueError("A benchmark is already running. Pause it before starting another.")
        spec = spec or self.plans.get(self.plans.default)
        execution = bind_candidate(spec, candidate)
        cohort = digest(execution["suite"])
        run = dict(
            id=uuid.uuid4().hex[:24],
            model_id=model_id,
            parallelism=parallelism,
            model=candidate["model"] if isinstance(candidate, dict) else candidate,
            snapshot=candidate.get("snapshot", "scripted")
            if isinstance(candidate, dict)
            else "scripted",
            candidate=candidate,
            candidate_id=execution["candidate_id"],
            plan_id=spec["id"],
            plan_name=spec["name"],
            plan_hash=digest(spec),
            cohort=cohort,
            runtime=runtime_fingerprint(),
            execution=execution,
            budget_track=candidate.get("budget_track", "accounted_compute")
            if isinstance(candidate, dict)
            else "accounted_compute",
            status="running",
            created_at=now(),
            updated_at=now(),
            completed_episodes=0,
            total_episodes=len(execution["episodes"]),
            current_episode=None,
            error=None,
            report=None,
        )
        self.launch(run)
        return self.summary(run)

    def launch(self, run):
        if self.active:
            raise ValueError("A benchmark is already running")
        if run["runtime"] != runtime_fingerprint():
            raise ValueError(
                "The benchmark engine changed. Start a new run; this run remains available for inspection."
            )
        resources = ExitStack()
        try:
            resources.enter_context(episode_lease(self.directory / "benchmark.lock"))
            run.update(status="running", error=None, updated_at=now())
            self.save(run)
            task = asyncio.create_task(self.execute(run))
            self.active[run["id"]] = (task, resources)
        except BaseException:
            resources.close()
            raise

    async def execute(self, run):
        try:
            folder = self.folder(run["id"])

            async def worker(manifest):
                result = await run_episode(manifest, folder / "episodes")
                if result["status"] != "completed":
                    raise ValueError("Episode did not complete")
                return result["diagnostics"]

            await execute_episodes(
                run,
                run["execution"]["episodes"],
                worker,
                lambda: self.save(run),
                run.get("parallelism", 1),
                "telemetry",
                [
                    "messages",
                    "provider_attempts",
                    "reported_input_tokens",
                    "reported_generated_tokens",
                    "invalid_envelopes",
                    "rejected_operations",
                ],
            )
            data = await asyncio.to_thread(collect, run["execution"], folder / "episodes")
            report = await asyncio.to_thread(summarize, data, run["execution"]["suite"])
            write_json(
                folder / "report.json",
                dict(report=report, results=data, suite=run["execution"]["suite"]),
            )
            run.update(status="completed", report=report, budget_track=report["budget_track"])
        except asyncio.CancelledError:
            run["status"] = "paused"
            raise
        except Exception as error:
            run.update(status="failed", error=f"{type(error).__name__}: {error}")
        finally:
            run["updated_at"] = now()
            self.save(run)
            active = self.active.pop(run["id"], None)
            if active:
                active[1].close()

    async def pause(self, ident):
        self.read(ident)
        active = self.active.get(ident)
        if active:
            active[0].cancel()
            await asyncio.gather(active[0], return_exceptions=True)
            # Cancellation before execute's first instruction still releases the lease.
            if ident in self.active:
                self.active.pop(ident)[1].close()
                run = self.read(ident)
                run.update(status="paused", updated_at=now())
                self.save(run)
        return self.summary(self.read(ident))

    async def resume(self, ident):
        run = self.read(ident)
        if run["status"] == "completed":
            raise ValueError("This benchmark is already complete")
        self.launch(run)
        return self.summary(run)

    async def shutdown(self):
        for ident in list(self.active):
            await self.pause(ident)

    def leaderboard(self, cohort, track):
        # Each exact candidate configuration appears once. Repeats are visible under Runs;
        # the latest complete run is used rather than cherry-picking its best score.
        latest = {}
        for run in sorted(self.runs(), key=lambda r: r["created_at"]):
            if (
                run["cohort"] == cohort
                and run["budget_track"] == track
                and run["status"] == "completed"
            ):
                latest[run["candidate_id"]] = run
        return sorted(latest.values(), key=lambda r: (-r["report"]["usi"], r["created_at"]))

    def episodes(self, ident):
        run = self.read(ident)
        result = []
        for index, manifest in enumerate(run["execution"]["episodes"]):
            folder = self.folder(ident) / "episodes" / manifest["run_id"]
            row = {
                k: manifest[k]
                for k in ("run_id", "domain", "level", "seed", "role", "replicate", "focal_slot")
            }
            row.update(index=index + 1, status="pending", completed_tick=0, score=None)
            if (folder / "episode.db").is_file():
                store = EventStore(folder / "episode.db", read_only=True)
                try:
                    row.update(store.status())
                finally:
                    store.close()
            if (folder / "result.json").is_file():
                metrics = read_json(folder / "result.json").get("metrics")
                if metrics:
                    weights = run["execution"]["suite"]["domains"][manifest["domain"]]["metrics"]
                    row["score"] = 100 * sum(
                        metrics[k]["normalized_value"] * weight for k, weight in weights.items()
                    )
            result.append(row)
        return result

    def episode_folder(self, ident, episode_id):
        run = self.read(ident)
        if episode_id not in {e["run_id"] for e in run["execution"]["episodes"]}:
            raise ValueError("Unknown episode in this benchmark")
        return self.folder(ident) / "episodes" / episode_id
