"""Society runs: start, pause, resume, live progress, reports and the leaderboard."""

import asyncio
import re
import uuid
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

from ..core.ids import digest
from ..persistence.json_files import read_json, write_json
from ..persistence.lease import episode_lease
from . import ENGINE
from .catalog import DOMAINS
from .runner import engine_fingerprint, live_view, run_seed
from .simulate import calibrate


def now():
    return datetime.now(timezone.utc).isoformat()


def recipe_hash(recipe):
    return digest(recipe)


class SocietyService:
    def __init__(self, directory, recipes, lock=None, explorer=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.recipes = recipes
        self.lock = Path(lock) if lock else self.directory / "unimatrix.lock"
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        self.active = {}
        self.live = {}
        self._references = {}
        self.explorer = Path(explorer) if explorer else None

    # ------------------------------------------------------------------ records
    def folder(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9]{24}", ident):
            raise ValueError("Unknown society run")
        return self.directory / ident

    def read(self, ident):
        path = self.folder(ident) / "run.json"
        if not path.is_file():
            raise FileNotFoundError(ident)
        return read_json(path)

    def save(self, run):
        run["updated_at"] = now()
        write_json(self.folder(run["id"]) / "run.json", run)

    def summary(self, run):
        result = {k: v for k, v in run.items() if k not in {"candidate", "recipe"}}
        if run["status"] == "running" and run["id"] not in self.active:
            result["status"] = "interrupted"
        weeks = sum(s["week"] for s in run["seeds"].values())
        result["progress"] = dict(weeks=weeks, total=run["weeks"] * len(run["seeds"]))
        return result

    def runs(self):
        rows = []
        for path in sorted(self.directory.glob("*/run.json"), reverse=True):
            try:
                rows.append(self.summary(read_json(path)))
            except (ValueError, OSError, KeyError):
                continue
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    # ------------------------------------------------------------------ references
    def references(self, recipe):
        key = recipe_hash(recipe)
        if key not in self._references:
            rows = calibrate(recipe, recipe["seeds"], ("passive", "naive", "expert", "oracle"))
            table = {}
            for policy in ("passive", "naive", "expert", "oracle"):
                chosen = [r for r in rows if r["policy"] == policy]
                table[policy] = dict(
                    total=round(fmean(r["total"] for r in chosen), 3),
                    collapsed=sum(1 for r in chosen if r["collapsed"]),
                    domains={d: round(fmean(r["domains"][d] for r in chosen), 3) for d in DOMAINS},
                    seeds={str(r["seed"]): r["total"] for r in chosen})
            self._references[key] = table
        return self._references[key]

    # ------------------------------------------------------------------ lifecycle
    async def start(self, candidate, model_id, recipe, parallelism=1):
        from ..models.config import is_model, validate_model

        validate_model(candidate)
        if not is_model(candidate):
            raise ValueError("A society run needs a model configuration")
        if type(parallelism) is not int or not 1 <= parallelism <= 8:
            raise ValueError("Parallel seeds must be between 1 and 8")
        if self.active:
            raise ValueError("A society run is already in progress. Pause it before starting another.")
        identity = {k: v for k, v in candidate.items()
                    if k not in {"endpoint", "api_key_file", "api_key_env", "input_price_per_million",
                                 "output_price_per_million"}}
        run = dict(
            id=uuid.uuid4().hex[:24], engine=ENGINE, engine_fingerprint=engine_fingerprint(),
            model_id=model_id, model=candidate["model"], snapshot=candidate.get("snapshot", ""),
            candidate=deepcopy(candidate), candidate_id=digest(identity),
            recipe_id=recipe["id"], recipe_name=recipe["name"], recipe_hash=recipe_hash(recipe),
            recipe=deepcopy(recipe), weeks=recipe["ticks"], parallelism=parallelism,
            seeds={str(seed): dict(seed=seed, week=0, status="pending", total=None, collapsed=None,
                                   metrics=None, points=None) for seed in recipe["seeds"]},
            status="running", created_at=now(), updated_at=now(), error=None, score=None,
            usage=dict(calls=0, input_tokens=0, generated_tokens=0, seconds=0.0),
        )
        self.folder(run["id"]).mkdir(parents=True, exist_ok=True)
        self.launch(run)
        return self.summary(run)

    def launch(self, run):
        if self.active:
            raise ValueError("A society run is already in progress")
        self.live.pop(run["id"], None)
        if run["engine_fingerprint"] != engine_fingerprint():
            raise ValueError("The society engine changed since this run started; start a new run "
                             "(the recorded run stays available).")
        resources = ExitStack()
        try:
            resources.enter_context(episode_lease(self.lock))
            run.update(status="running", error=None)
            self.save(run)
            task = asyncio.create_task(self.execute(run))
            self.active[run["id"]] = (task, resources)
        except BaseException:
            resources.close()
            raise

    async def execute(self, run):
        try:
            await self._execute(run)
        except asyncio.CancelledError:
            run["status"] = "paused"
            raise
        except Exception as error:  # recorded for the dashboard; the log allows a resume
            run.update(status="failed", error=f"{type(error).__name__}: {error}")
            for seed in run["seeds"].values():
                if seed["status"] == "running":
                    seed["status"] = "paused"
        finally:
            self.save(run)
            active = self.active.pop(run["id"], None)
            if active:
                active[1].close()

    async def _execute(self, run):
        from ..models.managed import ManagedModel
        from .llm import StewardPolicy

        recipe = run["recipe"]
        folder = self.folder(run["id"])
        pending = [s for s in run["seeds"].values() if s["status"] != "completed"]
        gate = asyncio.Semaphore(run["parallelism"])
        # One loaded model for the whole run: parallel seeds are concurrent predictions on it.
        instance = ManagedModel(run["candidate"])

        async def play(entry):
            async with gate:
                policy = StewardPolicy(run["candidate"], recipe, instance=instance)
                entry["status"] = "running"
                self.save(run)

                def on_week(world):
                    entry["week"] = min(world.week - 1, world.horizon) if not world.collapsed else world.collapsed["week"]
                    last = world.frames[-1]
                    entry["metrics"] = {k: last["metrics"].get(k) for k in
                                        ("population", "wellbeing", "trust", "legitimacy", "protest", "commons",
                                         "treasury", "granary", "gini")}
                    entry["points"] = round(sum(last["points"].values()), 2)
                    self.live.setdefault(run["id"], {})[str(entry["seed"])] = live_view(world)
                    self._account(run)
                    self.save(run)

                try:
                    report, world = await run_seed(recipe, entry["seed"], policy, folder / f"seed-{entry['seed']}",
                                                   on_week, meta=dict(run_id=run["id"], model=run["model"],
                                                                      snapshot=run["snapshot"]))
                finally:
                    await policy.close()
                self._account(run)
                entry.update(status="completed", week=world.horizon if not world.collapsed else world.collapsed["week"],
                             total=report["score"]["total"], collapsed=report["score"]["collapsed"],
                             earned=report["score"]["earned"])
                self.save(run)

        try:
            async with asyncio.TaskGroup() as seeds:  # a failing seed stops its siblings
                for entry in pending:
                    seeds.create_task(play(entry))
        except BaseExceptionGroup as failure:
            raise failure.exceptions[0] from None
        finally:
            await instance.close()
        run["score"] = self.aggregate(run)
        run["status"] = "completed"
        write_json(folder / "report.json", self.combined(run))
        self.export(run)

    def export(self, run):
        """Write the run's xCDN file for the static Explorer; never fails the run."""
        if self.explorer is None:
            return None
        from .explorer import ExplorerExport, recorded

        try:
            seeds, decisions = recorded(self.folder(run["id"]), run)
            path = ExplorerExport(self.explorer).write_run(dict(run, kind=run.get("kind", "model")), run["recipe"],
                                                          seeds, self.references(run["recipe"]), decisions)
        except (OSError, ValueError, KeyError, TypeError) as error:
            run["explorer_error"] = f"{type(error).__name__}: {error}"
            return None
        run["explorer_file"] = str(path) if path else None
        return path

    def _account(self, run):
        from .runner import read_log

        usage = dict(calls=0, input_tokens=0, generated_tokens=0, seconds=0.0)
        for seed in run["seeds"].values():
            for row in read_log(self.folder(run["id"]) / f"seed-{seed['seed']}" / "decisions.jsonl"):
                u = row.get("usage", {})
                usage["calls"] += 1
                usage["input_tokens"] += u.get("input_tokens") or 0
                usage["generated_tokens"] += u.get("generated_tokens") or 0
                usage["seconds"] += u.get("latency") or 0.0
        usage["seconds"] = round(usage["seconds"], 1)
        run["usage"] = usage

    def aggregate(self, run):
        reports = []
        for seed in run["seeds"].values():
            path = self.folder(run["id"]) / f"seed-{seed['seed']}" / "report.json"
            if path.is_file():
                reports.append(read_json(path))
        if not reports:
            return None
        totals = [r["score"]["total"] for r in reports]
        references = self.references(run["recipe"])
        expert = references["expert"]["total"] or None
        domains = {d: round(fmean(r["score"]["domains"][d]["points"] for r in reports), 3) for d in DOMAINS}
        return dict(
            total=round(fmean(totals), 3),
            totals=totals,
            collapsed=sum(1 for r in reports if r["score"]["collapsed"]),
            seeds=len(reports),
            domains=domains,
            wellby=round(fmean(r["score"]["wellby"] for r in reports), 3),
            relative_to_expert=round(fmean(totals) / expert, 3) if expert else None,
            references={k: v["total"] for k, v in references.items()},
        )

    def combined(self, run):
        reports = {}
        for seed in run["seeds"].values():
            path = self.folder(run["id"]) / f"seed-{seed['seed']}" / "report.json"
            if path.is_file():
                reports[str(seed["seed"])] = read_json(path)
        return dict(run=self.summary(run), score=run.get("score") or self.aggregate(run),
                    references=self.references(run["recipe"]), seeds=reports)

    async def pause(self, ident):
        self.read(ident)
        active = self.active.get(ident)
        if active:
            active[0].cancel()
            await asyncio.gather(active[0], return_exceptions=True)
            run = self.read(ident)
            run["status"] = "paused"
            for seed in run["seeds"].values():
                if seed["status"] == "running":
                    seed["status"] = "paused"
            self.save(run)
        return self.summary(self.read(ident))

    async def resume(self, ident):
        run = self.read(ident)
        if run["status"] == "completed":
            raise ValueError("This society run is already complete")
        self.launch(run)
        return self.summary(run)

    async def shutdown(self):
        for ident in list(self.active):
            await self.pause(ident)

    def delete(self, ident):
        if ident in self.active:
            raise ValueError("Pause the run before deleting it")
        folder = self.folder(ident)
        if not (folder / "run.json").is_file():
            raise FileNotFoundError(ident)
        import shutil

        shutil.rmtree(folder)
        if self.explorer is not None:
            from .explorer import ExplorerExport

            ExplorerExport(self.explorer).remove_run(ident)
        return dict(deleted=ident)

    # ------------------------------------------------------------------ views
    def report(self, ident):
        run = self.read(ident)
        path = self.folder(ident) / "report.json"
        if run["status"] == "completed" and path.is_file():
            return read_json(path)
        return self.combined(run)

    def live_frames(self, ident, seed, since=-1):
        """Frames after week `since` (all by default) and the cast, for live animated viewing."""
        run = self.read(ident)
        view = self.live.get(ident, {}).get(str(seed))
        if view is None:
            path = self.folder(ident) / f"seed-{seed}" / "report.json"
            if path.is_file():
                report = read_json(path)
                cast = [dict(id=r["id"], name=r["name"], age=r["age"], role=r["role"], faction=r["faction"],
                             newcomer=r["newcomer"], bio=r["bio"]) for r in report["residents"]]
                view = dict(frames=report.get("frames", []), residents=cast)
            elif (self.folder(ident) / f"seed-{seed}" / "decisions.jsonl").is_file():
                from .runner import replay_seed

                view = live_view(replay_seed(run["recipe"], int(seed), self.folder(ident) / f"seed-{seed}"))
            else:
                view = dict(frames=[], residents=[])
        if ident not in self.active:
            self.live.setdefault(ident, {})[str(seed)] = view
        frames = [f for f in view["frames"] if f["week"] > since]
        return dict(run=self.summary(run), frames=frames, residents=view["residents"],
                    timeline=[dict(f["metrics"], week=f["week"]) for f in frames])

    def recipe(self, ident):
        """The exact recipe revision a run used (frozen at start)."""
        run = self.read(ident)
        return dict(recipe=run["recipe"], hash=run["recipe_hash"], id=run["recipe_id"])

    def decisions(self, ident, seed, week=None):
        from .runner import read_log

        rows = read_log(self.folder(ident) / f"seed-{int(seed)}" / "decisions.jsonl")
        if week is not None:
            rows = [r for r in rows if r["week"] == week]
        return rows

    def leaderboard(self, recipe_id, revision=None):
        best = {}
        for run in self.runs():
            if run["recipe_id"] != recipe_id or run["status"] != "completed" or not run.get("score"):
                continue
            if revision is not None and run["recipe_hash"] != revision:
                continue
            key = (run["candidate_id"], run["recipe_hash"])
            if key not in best or run["created_at"] > best[key]["created_at"]:
                best[key] = run
        rows = sorted(best.values(), key=lambda r: -r["score"]["total"])
        return [dict(rank=i + 1, id=r["id"], model=r["model"], snapshot=r["snapshot"], model_id=r["model_id"],
                     total=r["score"]["total"], domains=r["score"]["domains"], collapsed=r["score"]["collapsed"],
                     seeds=r["score"]["seeds"], relative_to_expert=r["score"].get("relative_to_expert"),
                     wellby=r["score"].get("wellby"), created_at=r["created_at"], recipe_hash=r["recipe_hash"])
                for i, r in enumerate(rows)]


def estimate(recipe, seconds_per_decision, safety=1.3):
    decisions = recipe["ticks"] * len(recipe["seeds"])
    return dict(recipe=recipe["id"], decisions=decisions, seconds=round(decisions * seconds_per_decision * safety, 1),
                minutes=round(decisions * seconds_per_decision * safety / 60, 1))

