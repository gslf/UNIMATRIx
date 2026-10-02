"""The benchmark dashboard: fixed recipes, model files, runs and recorded evidence."""

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..benchmark.models import ModelRepository
from ..benchmark.recipes import BUNDLED, RecipeRepository, scoring_spec
from ..benchmark.service import BenchmarkService
from ..core.ids import digest
from ..persistence.event_store import EventStore
from ..persistence.json_files import read_json
from .deletion import Deletions
from .model_health import check_model


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    recipe_id: str | None = None
    profile: str | None = None
    cohort: str | None = None
    parallelism: int = Field(1, ge=1, le=64, strict=True)


class ModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict
    api_key: str | None = None


def build_router(
    directory,
    models_dir="config/models",
    recipes_dir="config/recipes",
    default_recipe="standard-v1",
):
    models = ModelRepository(models_dir)
    plans = RecipeRepository(recipes_dir, default_recipe)
    plans.all()
    service = BenchmarkService(directory, plans)
    deletions = Deletions(directory, plans)
    router = APIRouter(prefix="/api")

    def checked(fn, *args):
        try:
            return fn(*args)
        except FileNotFoundError as error:
            raise HTTPException(404, "File or run not found") from error
        except (ValueError, TypeError, KeyError) as error:
            raise HTTPException(422, str(error)) from error
        except OSError as error:
            raise HTTPException(500, "Could not read or write local benchmark data") from error

    async def checked_async(fn, *args):
        try:
            return await fn(*args)
        except FileNotFoundError as error:
            raise HTTPException(404, "File or run not found") from error
        except ValueError as error:
            message = (
                "A benchmark or research evaluation is already running. Pause it before starting another."
                if str(error) == "episode_already_running"
                else str(error)
            )
            raise HTTPException(409, message) from error

    @router.get("/recipes")
    def plan_list():
        rows = {}
        bundled = {read_json(p)["id"] for p in BUNDLED.glob("*.json")}
        for spec in checked(plans.all).values():
            scoring = scoring_spec(spec)
            cohort = digest(scoring)
            rows[cohort] = dict(
                id=cohort,
                recipe_id=spec["id"],
                name=spec["name"],
                description=spec["description"],
                cases=len(spec["cases"]),
                default=spec["id"] == plans.default,
                archived=False,
                deletable=spec["id"] not in bundled and spec["id"] != plans.default,
                deletion_note=("Bundled recipes are read-only." if spec["id"] in bundled
                               else "Restart with --default-recipe standard-v1 to delete this default recipe."
                               if spec["id"] == plans.default else ""),
                recipe_hash=digest(spec),
                runtime=scoring["runtime_fingerprint"],
                spec=spec,
            )
        for path in service.directory.glob("*/benchmark.json"):
            run = read_json(path)
            if run["cohort"] not in rows:
                scoring = run["execution"]["suite"]
                rows[run["cohort"]] = dict(
                    id=run["cohort"],
                    recipe_id=run["recipe_id"],
                    name=run["recipe_name"],
                    description=scoring["description"],
                    cases=run["total_episodes"],
                    default=False,
                    archived=True,
                    deletable=False,
                    deletion_note="Select the current saved recipe to delete all its revisions.",
                    recipe_hash=run["recipe_hash"],
                    runtime=run["runtime"],
                    spec=scoring,
                )
        return list(rows.values())

    @router.get("/models")
    def model_list():
        return models.all()

    @router.get("/personalities")
    def personalities():
        from ..benchmark.personas import catalog

        return catalog()

    @router.post("/models/{ident}/test")
    async def test_model(ident: str):
        return await check_model(checked(models.get, ident))

    @router.put("/models/{ident}")
    def save_model(ident: str, body: ModelIn):
        return checked(models.save_from_web, ident, body.config, body.api_key)

    @router.get("/benchmarks")
    def run_list(cohort: str | None = None):
        return [r for r in service.runs() if cohort is None or r["cohort"] == cohort]

    @router.post("/benchmarks")
    async def start(body: StartIn):
        candidate = checked(models.get, body.model_id)
        spec = checked(plans.get, body.recipe_id if body.recipe_id is not None else plans.default)
        if body.cohort is not None and body.cohort != digest(scoring_spec(spec)):
            raise HTTPException(
                409, "This recipe revision has changed or is archived. Refresh and select "
                "the current recipe before starting."
            )
        if body.profile is not None:
            from ..benchmark.personas import apply_profile

            candidate = checked(apply_profile, candidate, spec.get("candidate_profiles", {}), body.profile)
        return await checked_async(service.start, candidate, body.model_id, spec, body.parallelism)

    @router.get("/leaderboard")
    def leaderboard(cohort: str, track: str = "opaque_compute"):
        return service.leaderboard(cohort, track)

    @router.get("/leaderboard/stability")
    def stability(cohort: str, track: str = "opaque_compute"):
        return checked(service.stability, cohort, track)

    @router.get("/benchmarks/{ident}")
    def detail(ident: str):
        run = checked(service.read, ident)
        return dict(service.summary(run), candidate=run["candidate"], episodes=service.episodes(ident))

    @router.post("/benchmarks/{ident}/pause")
    async def pause(ident: str):
        return await checked_async(service.pause, ident)

    @router.get("/benchmarks/{ident}/deletion")
    def run_deletion(ident: str):
        return checked(lambda: deletions.public(deletions.benchmark(ident)))

    @router.delete("/benchmarks/{ident}")
    async def delete_run(ident: str):
        return checked(deletions.delete, deletions.benchmark, ident)

    @router.post("/benchmarks/{ident}/resume")
    async def resume(ident: str):
        return await checked_async(service.resume, ident)

    @router.get("/benchmarks/{ident}/export")
    def export(ident: str):
        run = checked(service.read, ident)
        if run["status"] != "completed":
            raise HTTPException(409, "Only complete benchmarks have scored reports")
        return FileResponse(service.folder(ident) / "report.json", filename=ident + "-report.json")

    def store_for(ident, episode_id):
        folder = checked(service.episode_folder, ident, episode_id)
        if not (folder / "episode.db").is_file():
            raise HTTPException(404, "This episode has not started yet")
        return EventStore(folder / "episode.db", read_only=True), folder

    @router.get("/benchmarks/{ident}/episodes/{episode_id}")
    def episode_detail(ident: str, episode_id: str):
        store, folder = store_for(ident, episode_id)
        try:
            series = []
            for tick, raw in store.db.execute("SELECT tick,state FROM snapshots ORDER BY tick"):
                state = json.loads(store._unpack(raw))
                series.append(
                    dict(
                        tick=tick,
                        living=sum(a["alive"] for a in state["agents"].values()),
                        resources={
                            resource: sum(
                                a["inventory"].get(resource, 0) for a in state["agents"].values()
                            )
                            / 1000
                            for resource in {
                                r for a in state["agents"].values() for r in a["inventory"]
                            }
                        },
                    )
                )
            result = read_json(folder / "result.json") if (folder / "result.json").is_file() else {}
            return dict(
                manifest=store.manifest,
                **store.status(),
                series=series,
                state=store.load().dump(),
                metrics=result.get("metrics", {}),
                diagnostics=result.get("diagnostics"),
                verification=result.get("verification"),
            )
        finally:
            store.close()

    @router.get("/benchmarks/{ident}/episodes/{episode_id}/state")
    def snapshot(ident: str, episode_id: str, tick: int = Query(ge=0)):
        store, _ = store_for(ident, episode_id)
        try:
            state = store.snapshot(tick)
            if state is None:
                raise HTTPException(404, "No snapshot at this tick")
            return state
        finally:
            store.close()

    @router.get("/benchmarks/{ident}/episodes/{episode_id}/events")
    def events(
        ident: str,
        episode_id: str,
        after: int = Query(0, ge=0),
        kind: str = "all",
        agent: str = "",
        limit: int = Query(100, ge=1, le=500),
    ):
        store, _ = store_for(ident, episode_id)
        try:
            clauses = [
                "seq > ?",
                "type NOT IN ('world_initialized','state_committed')",
            ]
            parameters = [after]
            if kind == "messages":
                clauses.append("type = 'message_sent'")
            if agent:
                clauses.append(
                    "(json_extract(body, '$.actor_id') = ? OR EXISTS "
                    "(SELECT 1 FROM json_each(json_extract(body, '$.payload.to')) WHERE value = ?))"
                )
                parameters.extend([agent, agent])
            parameters.append(limit + 1)
            rows = [
                json.loads(row[0])
                for row in store.db.execute(
                    "SELECT body FROM events WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY seq LIMIT ?",
                    parameters,
                )
            ]
            return dict(
                items=rows[:limit],
                more=len(rows) > limit,
                cursor=rows[min(len(rows), limit) - 1]["seq"] if rows else after,
            )
        finally:
            store.close()

    @router.get("/benchmarks/{ident}/episodes/{episode_id}/decision")
    def decision(ident: str, episode_id: str, agent: str, tick: int = Query(ge=0)):
        store, _ = store_for(ident, episode_id)
        try:
            row = store.db.execute(
                "SELECT observation,raw,request_id FROM decisions WHERE tick=? AND slot=?",
                (tick, agent),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "No recorded decision for this agent at this tick")
            calls = [
                json.loads(r[0])
                for r in store.db.execute(
                    "SELECT body FROM model_calls WHERE request_id=? ORDER BY attempt", (row[2],)
                )
            ]
            return dict(observation=store.observation(row[0]), response=row[1], calls=calls)
        finally:
            store.close()

    router.shutdown_tasks = service.shutdown
    router.service = service
    return router
