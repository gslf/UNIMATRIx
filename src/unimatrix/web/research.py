"""Recipe lab authoring, frozen campaign previews and diagnostics."""

import os
import re
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from ..benchmark.models import ModelRepository
from ..benchmark.recipes import BUNDLED, RecipeRepository, recipe_fields, recipe_text
from ..core.ids import digest
from ..persistence.json_files import read_json, write_json
from ..policies.scripted import BASELINES as PEER_POLICIES
from ..research.campaigns import BASELINES, INTERVENTIONS, CampaignRunner, prepare
from ..research.design import Design, build_design
from .deletion import Deletions
from .evaluation_explorer import add_explorer_routes


class CampaignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    split: Literal["development", "holdout"] = "development"
    name: str = Field(min_length=1, max_length=120)
    parallelism: int = Field(1, ge=1, le=64, strict=True)
    model_ids: list[str] = Field(default_factory=list, max_length=8)
    baselines: list[str] = Field(default_factory=lambda: list(BASELINES), max_length=6)
    interventions: list[str] = Field(
        default_factory=lambda: ["no_communication", "no_memory"], max_length=4
    )


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    split: Literal["development", "holdout"] = "development"


class RecipeDesign(Design):
    base_plan: str = Field(
        default="compact-v1", validation_alias=AliasChoices("base_recipe", "base_plan")
    )


def build_research_router(runs_dir, models_dir, recipes_dir, default_recipe):
    root = Path(runs_dir) / "research"
    root.mkdir(parents=True, exist_ok=True)
    models = ModelRepository(models_dir)
    plans = RecipeRepository(recipes_dir, default_recipe)
    runner = CampaignRunner(root / "campaigns", Path(runs_dir) / "benchmark.lock")
    deletions = Deletions(runs_dir, plans)
    router = APIRouter()

    def checked(fn, *args):
        try:
            return fn(*args)
        except FileNotFoundError as error:
            raise HTTPException(404, "Research record not found") from error
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            message = (
                "Another benchmark or research evaluation is running."
                if str(error) == "episode_already_running"
                else recipe_text(str(error))
            )
            raise HTTPException(422, message) from error
        except OSError as error:
            raise HTTPException(500, "Could not read or write research data") from error

    def folder(kind, ident):
        if not re.fullmatch(r"[a-f0-9]{24}", ident):
            raise HTTPException(404, "Unknown research record")
        return root / kind / ident

    def draft(ident):
        return checked(read_json, folder("drafts", ident) / "draft.json")

    def campaign(ident):
        return checked(read_json, folder("campaigns", ident) / "campaign.json")

    def public(record):
        result = {k: v for k, v in record.items() if k not in {"studies", "plan"}}
        result["studies"] = [
            {k: v for k, v in s.items() if k != "execution"} for s in record["studies"]
        ]
        result["plan_name"] = record["plan"]["name"]
        if result["status"] == "running" and record["id"] not in runner.active:
            result["status"] = "interrupted"
        result["completed_episodes"] = sum(s["completed_episodes"] for s in record["studies"])
        return recipe_fields(result)

    @router.get("/drafts/{ident}/deletion")
    async def draft_deletion(ident: str):
        return deletions.public(deletions.recipe(ident, True))

    @router.delete("/drafts/{ident}")
    async def delete_draft(ident: str):
        return deletions.delete(deletions.recipe, ident, True)

    @router.get("/recipes/{ident}/deletion")
    async def recipe_deletion(ident: str):
        return deletions.public(deletions.recipe(ident))

    @router.delete("/recipes/{ident}")
    async def delete_recipe(ident: str):
        return deletions.delete(deletions.recipe, ident)

    @router.delete("/evaluations/{ident}")
    @router.delete("/campaigns/{ident}", include_in_schema=False)
    async def delete_evaluation(ident: str):
        return deletions.delete(deletions.evaluation, ident)

    @router.get("/options")
    def options():
        return recipe_fields(
            dict(
                plans=list(checked(plans.all).values()),
                models=models.all(),
                baselines=BASELINES,
                interventions=INTERVENTIONS,
                peer_policies=PEER_POLICIES,
                metric_catalog=read_json(BUNDLED.parent / "core-v1.json")["domains"],
            )
        )

    @router.get("/drafts")
    def drafts():
        return [
            recipe_fields(
                dict(
                    id=r["id"],
                    name=r["plan"]["name"],
                    plan_id=r["plan"]["id"],
                    episodes=r["episodes"],
                )
            )
            for p in sorted(
                (root / "drafts").glob("*/draft.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for r in [read_json(p)]
        ]

    @router.post("/drafts")
    async def generate(body: RecipeDesign):
        record = checked(build_design, body, plans)
        record["id"] = uuid.uuid4().hex[:24]
        target = folder("drafts", record["id"])
        checked(write_json, target / "draft.json", record)
        checked(write_json, target / "development.json", record["plan"])
        if record["holdout"]:
            checked(write_json, target / "holdout.json", record["holdout"])
        return recipe_fields(record)

    @router.get("/drafts/{ident}")
    def read_draft(ident: str):
        return recipe_fields(draft(ident))

    @router.get("/drafts/{ident}/export")
    def export_draft(ident: str, split: Literal["development", "holdout"] = "development"):
        record = draft(ident)
        spec = record["plan"] if split == "development" else record["holdout"]
        if not spec:
            raise HTTPException(422, "This draft has no holdout benchmark recipe")
        return FileResponse(
            folder("drafts", ident) / (split + ".json"), filename=spec["id"] + ".json"
        )

    @router.post("/drafts/{ident}/publish")
    async def publish(ident: str, body: PublishIn):
        record = draft(ident)
        spec = record["plan"] if body.split == "development" else record["holdout"]
        if not spec:
            raise HTTPException(422, "This draft has no holdout benchmark recipe")
        existing = checked(plans.all).get(spec["id"])
        if existing:
            if existing == spec:
                return dict(id=spec["id"], name=spec["name"], already_saved=True)
            raise HTTPException(
                409, "This benchmark recipe ID already exists. Build a draft with a new version ID."
            )
        target_dir = plans.directory
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / (spec["id"] + ".json")
        temporary = target_dir / (".publish-" + uuid.uuid4().hex + ".tmp")
        try:
            checked(write_json, temporary, spec)
            # Atomic creation without replacing an existing author's file.
            os.link(temporary, target)
        except FileExistsError as error:
            raise HTTPException(
                409, "A file with this benchmark recipe ID already exists"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)
        return dict(id=spec["id"], name=spec["name"], already_saved=False)

    @router.post("/evaluation-preview")
    @router.post("/campaign-preview", include_in_schema=False)
    async def preview(body: CampaignIn):
        record = draft(body.draft_id)
        spec = record["plan"] if body.split == "development" else record["holdout"]
        if not spec:
            raise HTTPException(422, "This draft has no holdout benchmark recipe")
        if len(body.model_ids) != len(set(body.model_ids)):
            raise HTTPException(422, "Select each model only once")
        selected = {ident: checked(models.get, ident) for ident in body.model_ids}
        prepared = checked(prepare, spec, selected, body.baselines, body.interventions, body.name, body.parallelism)
        prepared.update(draft_id=body.draft_id, split=body.split)
        ident = digest(
            {
                "request": body.model_dump(),
                "plan": spec,
                "models": selected,
                "runtime": prepared["runtime"],
                "research_runtime": prepared["research_runtime"],
            }
        )[:24]
        prepared["id"] = ident
        checked(write_json, folder("previews", ident) / "preview.json", prepared)
        return public(prepared)

    @router.post("/evaluations")
    @router.post("/campaigns", include_in_schema=False)
    async def launch(body: StartIn):
        prepared = checked(read_json, folder("previews", body.preview_id) / "preview.json")
        prepared["id"] = uuid.uuid4().hex[:24]
        checked(runner.launch, prepared)
        return public(prepared)

    @router.get("/evaluations")
    @router.get("/campaigns", include_in_schema=False)
    def campaign_list():
        return [
            public(read_json(p))
            for p in sorted(
                (root / "campaigns").glob("*/campaign.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        ]

    @router.get("/evaluations/{ident}")
    @router.get("/campaigns/{ident}", include_in_schema=False)
    def read_campaign(ident: str):
        return public(campaign(ident))

    @router.post("/evaluations/{ident}/pause")
    @router.post("/campaigns/{ident}/pause", include_in_schema=False)
    async def pause(ident: str):
        await runner.pause(campaign(ident))
        return public(campaign(ident))

    @router.post("/evaluations/{ident}/resume")
    @router.post("/campaigns/{ident}/resume", include_in_schema=False)
    async def resume(ident: str):
        record = campaign(ident)
        if record["status"] in {"completed", "completed_with_failures", "failed"}:
            raise HTTPException(
                409, "Create a new evaluation to repeat a finished or failed experiment"
            )
        checked(runner.launch, record)
        return public(record)

    @router.get("/evaluations/{ident}/export")
    @router.get("/campaigns/{ident}/export", include_in_schema=False)
    def export_report(ident: str):
        record = campaign(ident)
        output = folder("campaigns", ident) / "findings.json"
        checked(write_json, output, dict(public(record), recipe=record["plan"]))
        return FileResponse(output, filename=ident + "-findings.json")

    add_explorer_routes(router, campaign, folder, runner)
    current = APIRouter(prefix="/api/recipe-lab")
    current.include_router(router)
    legacy = APIRouter(prefix="/api/research")
    legacy.include_router(router)
    current.legacy_router = legacy
    current.shutdown_tasks = runner.shutdown
    current.runner = runner
    return current
