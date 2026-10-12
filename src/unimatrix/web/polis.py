"""Society Lab and society runs: author a society, preview it with reference stewards,
run a candidate model as steward, and read the sociological report."""

import asyncio

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ..models.repository import ModelRepository
from ..polis.recipe import DEFAULT, PolisRecipes, RecipeError, catalog, normalize, summary
from ..polis.service import SocietyService, estimate, recipe_hash
from ..polis.simulate import calibrate, simulate, suggested_rates

POLICIES = ("passive", "random", "naive", "expert", "oracle")


class SimulateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe: dict
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    policy: str = "expert"
    frames: bool = True


class CalibrateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe: dict
    seeds: list[int] | None = Field(None, max_length=12)
    policies: list[str] | None = Field(None, max_length=5)
    target: float = Field(1000.0, gt=0, le=1e7)


class RecipeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe: dict


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    recipe_id: str
    parallelism: int = Field(1, ge=1, le=8, strict=True)
    profile: str | None = None


def build_polis_router(runs_dir="runs", models_dir="config/models", societies_dir="config/societies",
                       explorer_dir=None):
    recipes = PolisRecipes(societies_dir)
    models = ModelRepository(models_dir)
    service = SocietyService(runs_dir, recipes, explorer=explorer_dir)
    router = APIRouter(prefix="/api/polis")

    def checked(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FileNotFoundError as error:
            raise HTTPException(404, "Not found") from error
        except RecipeError as error:
            raise HTTPException(422, str(error)) from error
        except (ValueError, KeyError, TypeError) as error:
            message = ("A benchmark or society run is already in progress. Pause it before starting another."
                       if str(error) == "episode_already_running" else str(error))
            raise HTTPException(422, message) from error

    async def offload(fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except RecipeError as error:
            raise HTTPException(422, str(error)) from error
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/catalog")
    def get_catalog():
        return catalog()

    @router.get("/recipes")
    def list_recipes():
        bundled = recipes.bundled_ids()
        rows = []
        for recipe in checked(recipes.all).values():
            rows.append(dict(summary(recipe), bundled=recipe["id"] in bundled, hash=recipe_hash(recipe),
                             tags=recipe.get("tags", []), author=recipe.get("author", "")))
        return sorted(rows, key=lambda r: (not r["bundled"], r["id"] != DEFAULT, r["id"]))

    @router.get("/recipes/{ident}")
    def get_recipe(ident: str):
        try:
            raw = recipes.raw(ident)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        normalized = checked(normalize, raw)
        return dict(raw=raw, recipe=normalized, bundled=ident in recipes.bundled_ids(), hash=recipe_hash(normalized))

    @router.post("/recipes/validate")
    def validate_recipe(body: RecipeIn):
        try:
            normalized = normalize(body.recipe)
        except RecipeError as error:
            return dict(ok=False, error=str(error))
        return dict(ok=True, summary=summary(normalized), warnings=warnings(normalized), hash=recipe_hash(normalized))

    @router.put("/recipes/{ident}")
    def save_recipe(ident: str, body: RecipeIn):
        if body.recipe.get("id") != ident:
            raise HTTPException(422, "The recipe id must match the address")
        normalized = checked(recipes.save, body.recipe)
        return dict(summary(normalized), hash=recipe_hash(normalized))

    @router.delete("/recipes/{ident}")
    def delete_recipe(ident: str):
        checked(recipes.delete, ident)
        return dict(deleted=ident)

    @router.post("/simulate")
    async def run_simulation(body: SimulateIn):
        if body.policy not in POLICIES:
            raise HTTPException(422, "Unknown reference steward")
        return await offload(simulate, body.recipe, body.seed, body.policy, body.frames)

    @router.post("/calibrate")
    async def run_calibration(body: CalibrateIn):
        policies = tuple(body.policies or POLICIES)
        if any(p not in POLICIES for p in policies):
            raise HTTPException(422, "Unknown reference steward")

        def work():
            recipe = normalize(body.recipe)
            seeds = body.seeds or recipe["seeds"]
            rows = calibrate(recipe, seeds, policies)
            rates = suggested_rates(rows, recipe, body.target) if "expert" in policies else None
            return dict(rows=rows, suggested_rates=rates, seeds=seeds)

        return await offload(work)

    @router.get("/estimate")
    def get_estimate(recipe: str, seconds: float = Query(15.0, gt=0, le=3600)):
        return checked(estimate, checked(recipes.get, recipe), seconds)

    @router.post("/estimate")
    def post_estimate(body: RecipeIn, seconds: float = Query(15.0, gt=0, le=3600)):
        """Estimate for an unsaved recipe being edited in the Lab."""
        return checked(estimate, checked(normalize, body.recipe), seconds)

    @router.get("/runs")
    def list_runs(recipe: str | None = None):
        return [r for r in service.runs() if recipe is None or r["recipe_id"] == recipe]

    @router.post("/runs")
    async def start_run(body: RunIn):
        candidate = checked(models.get, body.model_id)
        recipe = checked(recipes.get, body.recipe_id)
        if body.profile:
            raise HTTPException(422, "Personas are not supported for society runs yet")
        try:
            return await service.start(candidate, body.model_id, recipe, body.parallelism)
        except ValueError as error:
            message = ("A benchmark or society run is already in progress. Pause it before starting another."
                       if str(error) == "episode_already_running" else str(error))
            raise HTTPException(409, message) from error

    @router.get("/runs/{ident}")
    def get_run(ident: str):
        return service.summary(checked(service.read, ident))

    @router.post("/runs/{ident}/pause")
    async def pause_run(ident: str):
        checked(service.read, ident)
        return await service.pause(ident)

    @router.post("/runs/{ident}/resume")
    async def resume_run(ident: str):
        checked(service.read, ident)
        try:
            return await service.resume(ident)
        except ValueError as error:
            message = ("A benchmark or society run is already in progress. Pause it before starting another."
                       if str(error) == "episode_already_running" else str(error))
            raise HTTPException(409, message) from error

    @router.delete("/runs/{ident}")
    def delete_run(ident: str):
        return checked(service.delete, ident)

    @router.get("/runs/{ident}/recipe")
    def run_recipe(ident: str):
        return checked(service.recipe, ident)

    @router.get("/runs/{ident}/report")
    def run_report(ident: str):
        return checked(service.report, ident)

    @router.get("/runs/{ident}/timeline.csv")
    def run_timeline_csv(ident: str, seed: int = Query(..., ge=0)):
        """Weekly indicators and per-resident states as CSV, for analysis outside UNIMATRIx."""
        report = checked(service.report, ident)
        seed_report = report.get("seeds", {}).get(str(seed))
        if seed_report is None:
            raise HTTPException(404, "No report for this seed yet")
        return Response(timeline_csv(seed_report), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{ident}-seed{seed}-timeline.csv"'})

    @router.get("/runs/{ident}/live")
    def run_live(ident: str, seed: int = Query(..., ge=0), since: int = Query(-1, ge=-1)):
        return checked(service.live_frames, ident, seed, since)

    @router.get("/runs/{ident}/decisions")
    def run_decisions(ident: str, seed: int = Query(..., ge=0), week: int | None = Query(None, ge=1)):
        checked(service.read, ident)
        rows = checked(service.decisions, ident, seed, week)
        return [dict(week=r["week"], raw=r["raw"], usage=r.get("usage"), parse_error=r.get("parse_error"),
                     observation=r.get("observation")) for r in rows]

    @router.get("/leaderboard")
    async def leaderboard(recipe: str = DEFAULT):
        try:
            current = recipes.get(recipe)
        except ValueError:
            return dict(rows=service.leaderboard(recipe), references=None, revision=None)
        revision = recipe_hash(current)
        references = await asyncio.to_thread(service.references, current)
        return dict(rows=service.leaderboard(recipe, revision), references=references, revision=revision)

    router.service = service
    router.recipes = recipes
    router.shutdown_tasks = service.shutdown
    return router


def warnings(recipe):
    """Authoring hints: things that are valid but probably unintended."""
    found = []
    ids = {n["id"] for n in recipe["story"]["nodes"]}
    targets = {e["to"] for n in recipe["story"]["nodes"] for e in n["edges"]}
    for node in recipe["story"]["nodes"]:
        if node["trigger"] is None and node["id"] not in targets:
            found.append(f"Story node '{node['id']}' has no trigger and no incoming edge: it can never happen.")
    for node in recipe["story"]["nodes"]:
        for edge in node["edges"]:
            if edge["to"] not in ids:
                found.append(f"Edge from '{node['id']}' points to a missing node.")
    if not any(f["probe_week"] for f in recipe["facts"]):
        found.append("No fact asks the steward for a forecast: D1 forecasting points cannot be earned.")
    if not recipe["story"]["nodes"]:
        found.append("The story graph is empty: only the emergent dynamics will challenge the steward.")
    present = {r["id"] for r in recipe["residents"]}
    tied = {a for a, b, _ in recipe["ties"]} | {b for a, b, _ in recipe["ties"]}
    lonely = sorted(present - tied)
    if lonely:
        found.append("Residents without any tie (they start isolated): " + ", ".join(lonely))
    return found


def timeline_csv(report):
    import csv
    import io

    stream = io.StringIO()
    writer = csv.writer(stream)
    columns = ["week", "population", "wellbeing", "trust", "legitimacy", "grievance", "stress", "loneliness",
               "protest", "starving", "sick", "commons", "harvest", "treasury", "granary", "networth", "gini",
               "cohesion", "integration", "distressed", "isolated"]
    domains = sorted(report["score"]["domains"])
    writer.writerow(["scope", "id"] + columns + [f"points_{d}" for d in domains])
    for row in report["timeline"]:
        writer.writerow(["society", ""] + [row.get(c) for c in columns] + [row["points"].get(d) for d in domains])
    writer.writerow([])
    fields = ["week", "status", "activity", "wb", "st", "lo", "gr", "tr", "lg", "hp", "coins", "food", "protest",
              "sick", "hungry", "concern"]
    writer.writerow(["scope", "id"] + fields)
    for frame in report.get("frames", []):
        for r in frame["residents"]:
            concern = r["concern"]["kind"] if r.get("concern") else ""
            writer.writerow(["resident", r["id"], frame["week"]] + [r.get(f) for f in fields[1:-1]] + [concern])
    return stream.getvalue()

