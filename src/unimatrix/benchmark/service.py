"""Persistent benchmark runs and leaderboards over immutable, named plans."""

import asyncio
import re
import uuid
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from ..core.ids import digest
from ..evaluation.references import ensure_references
from ..evaluation.scoring import summarize
from ..persistence.event_store import EventStore
from ..persistence.json_files import read_json, write_json
from ..persistence.lease import episode_lease
from .duration import MAX_SECONDS, BudgetClock, recover, remaining
from .fingerprints import runtime_fingerprint
from .manifests import case_fields
from .parallel import execute_episodes, validate_parallelism
from .recipes import bind_candidate, scoring_spec
from .scheduler import collect, run_episode_offloaded
from .validation import is_model, scripted_name


def now():
    return datetime.now(timezone.utc).isoformat()


class BenchmarkService:
    def __init__(self, directory, plans):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.plans = plans
        self.active = {}
        self._clocks = {}
        self._stability_cache = {}

    def folder(self, ident):
        if not re.fullmatch(r"[a-f0-9]{24}", ident):
            raise ValueError("Unknown benchmark run")
        return self.directory / ident

    def read(self, ident):
        return read_json(self.folder(ident) / "benchmark.json")

    def save(self, run):
        if run["id"] in self._clocks:
            self._clocks[run["id"]].checkpoint()
        write_json(self.folder(run["id"]) / "benchmark.json", run)

    def summary(self, run):
        result = {k: v for k, v in run.items() if k not in {"execution", "candidate", "frozen_recipe"}}
        if run["status"] == "running" and run["id"] not in self.active:
            result["status"] = "interrupted"
        ticks = run["execution"]["episodes"][0]["ticks"] if run["execution"]["episodes"] else 240
        completed_ticks = run["completed_episodes"] * ticks
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
        result["max_wall_seconds"] = run["frozen_recipe"].get("max_wall_seconds", MAX_SECONDS)
        used = run.get("wall_seconds", 0.0)
        checkpoint = run.get("budget_checkpoint_at")
        if checkpoint:
            used += max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(checkpoint)).total_seconds())
        result["wall_seconds"] = used
        result["remaining_wall_seconds"] = max(0, result["max_wall_seconds"] - used)
        result["completed_ticks"] = completed_ticks
        result["total_ticks"] = run["total_episodes"] * ticks
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
        if candidate == "oracle":
            raise ValueError("The oracle is a research reference and never competes")
        spec = spec or self.plans.get(self.plans.default)
        execution = bind_candidate(spec, candidate)
        cohort = digest(execution["suite"])
        run = dict(
            id=uuid.uuid4().hex[:24],
            model_id=model_id,
            parallelism=parallelism,
            model=candidate["model"] if is_model(candidate) else scripted_name(candidate),
            snapshot=candidate.get("snapshot", "scripted") if is_model(candidate) else "scripted",
            candidate=candidate,
            candidate_id=execution["candidate_id"],
            recipe_id=spec["id"],
            recipe_name=spec["name"],
            recipe_hash=digest(spec),
            frozen_recipe=deepcopy(spec),
            cohort=cohort,
            runtime=runtime_fingerprint(),
            execution=execution,
            budget_track=candidate.get("budget_track", "accounted_compute")
            if is_model(candidate)
            else "accounted_compute",
            status="running",
            created_at=now(),
            updated_at=now(),
            completed_episodes=0,
            wall_seconds=0.0,
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
        frozen = run["frozen_recipe"]
        if digest(frozen) != run["recipe_hash"] or scoring_spec(frozen) != run["execution"]["suite"]:
            raise ValueError("frozen_recipe_mismatch")
        run["frozen_recipe"] = frozen
        recover(run)
        if not remaining(run) or run["status"] == "budget_exhausted":
            run.update(status="budget_exhausted", error="recipe_wall_time_budget_exhausted", updated_at=now())
            self.save(run)
            raise ValueError("recipe_wall_time_budget_exhausted")
        resources = ExitStack()
        try:
            resources.enter_context(episode_lease(self.directory / "benchmark.lock"))
            run.update(status="running", error=None, updated_at=now())
            self._clocks[run["id"]] = BudgetClock(run)
            self.save(run)
            task = asyncio.create_task(self.execute(run))
            self.active[run["id"]] = (task, resources)
        except BaseException:
            clock = self._clocks.pop(run["id"], None)
            if clock:
                clock.close()
            resources.close()
            raise

    async def execute(self, run):
        try:
            async with asyncio.timeout(remaining(run)):
                await self._execute_work(run)
        except TimeoutError:
            run.update(status="budget_exhausted", error="recipe_wall_time_budget_exhausted")
        except asyncio.CancelledError:
            run["status"] = "paused"
            raise
        except Exception as error:
            run.update(status="failed", error=f"{type(error).__name__}: {error}")
        finally:
            clock = self._clocks.pop(run["id"], None)
            if clock:
                clock.close()
            run["updated_at"] = now()
            self.save(run)
            active = self.active.pop(run["id"], None)
            if active:
                active[1].close()

    async def _execute_work(self, run):
        folder = self.folder(run["id"])

        async def worker(manifest):
            result = await run_episode_offloaded(manifest, folder / "episodes")
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
                "candidate_resolved_decisions",
                "candidate_invalid_envelopes",
                "candidate_rejected_operations",
                "candidate_messages",
                "candidate_decision_attempts",
                "candidate_infrastructure_errors",
                "invalid_envelopes",
                "rejected_operations",
            ],
        )
        data = await asyncio.to_thread(collect, run["execution"], folder / "episodes")
        references = None
        try:
            references = await ensure_references(
                run["frozen_recipe"],
                self.directory,
                run["execution"],
                lambda: self.save(run),
                run.get("parallelism", 1),
            )
        except ValueError as error:
            run["reference_error"] = f"{type(error).__name__}: {error}"
        report = await asyncio.to_thread(
            summarize, data, run["execution"]["suite"], references
        )
        execution_configuration = dict(runtime=run["runtime"], parallelism=run.get("parallelism", 1))
        write_json(
            folder / "report.json",
            dict(report=report, results=data, suite=run["execution"]["suite"],
                 model_configuration_sha256=digest(run["candidate"]),
                 execution_configuration=execution_configuration,
                 execution_configuration_sha256=digest(execution_configuration),
                 references=[dict(case=list(k), floor=v[0], anchor=v[1])
                             for k, v in sorted((references or {}).items())]),
        )
        run.update(status="completed", report=report, budget_track=report["budget_track"])

    async def pause(self, ident):
        self.read(ident)
        active = self.active.get(ident)
        if active:
            active[0].cancel()
            await asyncio.gather(active[0], return_exceptions=True)

            if ident in self.active:
                self.active.pop(ident)[1].close()
                clock = self._clocks.pop(ident, None)
                if clock:
                    clock.close()
                    run = clock.record
                else:
                    run = self.read(ident)
                    recover(run)
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

    def _eligible(self, cohort, track):


        latest = {}
        for run in sorted(self.runs(), key=lambda r: r["created_at"]):
            if (
                run["cohort"] == cohort
                and run["budget_track"] == track
                and run["status"] == "completed"
                and run.get("report", {}).get("scoring_version") == "reference-gain-v1"
                and run["report"].get("rating") is not None
            ):
                latest[run["candidate_id"]] = run
        return sorted(latest.values(), key=lambda r: r["candidate_id"])

    def leaderboard(self, cohort, track):
        rows = self._eligible(cohort, track)
        if len(rows) < 2:
            return [dict(r, dominance=dict(front=None, dominates=[], dominated_by=[])) for r in rows]
        analysis = self.stability(cohort, track)
        by_id = {r["candidate_id"]: r for r in rows}
        return [dict(by_id[item["system"]], dominance=item)
                for item in analysis["primary_order"]["ranking"]]

    def stability(self, cohort, track):
        from ..evaluation.stability import ranking_stability

        rows = self._eligible(cohort, track)
        if len(rows) < 2:
            return dict(available=False, reason="At least two complete calibrated candidates required")
        files = [self.folder(r["id"]) / "report.json" for r in rows]
        key = (cohort, track, tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files))
        if key in self._stability_cache:
            return self._stability_cache[key]
        exports = [read_json(p) for p in files]
        if any(e["suite"] != exports[0]["suite"] or e.get("references") != exports[0].get("references")
               for e in exports):
            raise ValueError("Mismatched ranking suites or reference anchors")
        refs = {tuple(r["case"]): (r["floor"], r["anchor"]) for r in exports[0]["references"]}
        result = dict(available=True, labels={r["candidate_id"]: r["model"] + " / " + r["snapshot"] for r in rows}, **ranking_stability(
            [e["results"] for e in exports], exports[0]["suite"], refs))
        self._stability_cache = {key: result}
        return result

    def episodes(self, ident):
        run = self.read(ident)
        result = []
        for index, manifest in enumerate(run["execution"]["episodes"]):
            folder = self.folder(ident) / "episodes" / manifest["run_id"]
            row = dict(
                case_fields(manifest),
                run_id=manifest["run_id"],
                focal_slot=manifest["focal_slot"],
                ticks=manifest["ticks"],
            )
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
