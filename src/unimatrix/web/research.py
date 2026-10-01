"""Recipe lab authoring, frozen campaign previews and diagnostics."""

import os
import re
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..benchmark.models import ModelRepository
from ..benchmark.personas import catalog as personalities
from ..benchmark.personas import societies, templates
from ..benchmark.recipes import BUNDLED, RecipeRepository
from ..core.ids import digest
from ..persistence.json_files import read_json, write_json
from ..policies.scripted import BASELINES as PEER_POLICIES
from ..policies.scripted import BY_NAME, DEFAULTS
from ..research.campaigns import BASELINES, INTERVENTIONS, CampaignRunner, prepare
from ..research.comparison import compare_smoke, compatible
from ..research.debugger import metric_report
from ..research.design import Design, build_design
from ..research.probes import ProbeRunner, harvest, observable_references
from ..research.structure import society_structure
from ..scenarios.layers import PRESETS, catalog, event_catalog, family_label, layers_key, ranges
from .deletion import Deletions
from .evaluation_explorer import add_explorer_routes


def recipe_fields(record):
    names = {
        "plan": "recipe",
        "plans": "recipes",
        "plan_id": "recipe_id",
        "plan_name": "recipe_name",
        "plan_hash": "recipe_hash",
    }
    return {names.get(key, key): value for key, value in record.items()}


class CampaignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    split: Literal["development", "holdout"] = "development"
    name: str = Field(min_length=1, max_length=120)
    parallelism: int = Field(1, ge=1, le=64, strict=True)
    model_ids: list[str] = Field(default_factory=list, max_length=32)
    baselines: list[str] = Field(default_factory=lambda: list(BASELINES), max_length=len(BASELINES))
    interventions: list[str] = Field(
        default_factory=lambda: ["no_communication", "no_memory"], max_length=4
    )
    mode: Literal["smoke", "pilot", "full"] = "full"
    stopping: dict | None = None
    profiles: list[str] = Field(default_factory=list, max_length=6)


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str


class ScreenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_ids: list[str] = Field(min_length=1, max_length=32)
    parallelism: int = Field(4, ge=1, le=64, strict=True)


class StructureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    design: Design
    domain: str
    seed: int | None = None
    role: Literal["advantaged", "disadvantaged"] | None = None
    stratum: int = Field(0, ge=0, le=2)
    certify: bool = False


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    split: Literal["development", "holdout"] = "development"


def build_research_router(runs_dir, models_dir, recipes_dir, default_recipe):
    root = Path(runs_dir) / "research"
    root.mkdir(parents=True, exist_ok=True)
    models = ModelRepository(models_dir)
    plans = RecipeRepository(recipes_dir, default_recipe)
    runner = CampaignRunner(root / "campaigns", Path(runs_dir) / "benchmark.lock")
    probes = ProbeRunner(root / "probes")
    deletions = Deletions(runs_dir, plans)
    router = APIRouter(prefix="/api/recipe-lab")

    def checked(fn, *args):
        try:
            return fn(*args)
        except FileNotFoundError as error:
            raise HTTPException(404, "Research record not found") from error
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            message = (
                "Another benchmark or research evaluation is running."
                if str(error) == "episode_already_running"
                else str(error)
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
            dict(
                {k: v for k, v in s.items() if k != "execution"},
                total_episodes=len(s["execution"]["episodes"]),
            )
            for s in record["studies"]
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
                metric_catalog=read_json(BUNDLED.parent / "metric-catalog.json")["domains"],
                complexity_presets=PRESETS,
                layer_catalog=catalog(),
                personalities=personalities(),
                societies=societies(),
                templates=templates(),
                event_catalog=event_catalog(),
                dispositions=dict(
                    defaults=DEFAULTS,
                    presets=BY_NAME,
                    types={key: type(value).__name__ for key, value in DEFAULTS.items()},
                ),
            )
        )

    @router.post("/structure")
    def structure(body: StructureIn):
        """The social structure of one case of the form's design; nothing is saved."""
        record = checked(build_design, body.design, plans)
        if body.stratum >= len(body.design.complexity):
            raise HTTPException(422, "Unknown complexity stratum")
        entry = body.design.complexity[body.stratum]
        label = family_label(entry) if ranges(entry) else layers_key(entry)
        return checked(
            society_structure, record["plan"], body.domain, body.seed, body.role, label, body.certify
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
    async def generate(body: Design):
        checked(models.validate_peer_credentials, (body.peers or []) + (body.holdout_peers or []))
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

            os.link(temporary, target)
        except FileExistsError as error:
            raise HTTPException(
                409, "A file with this benchmark recipe ID already exists"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)
        return dict(id=spec["id"], name=spec["name"], already_saved=False)

    @router.post("/evaluation-preview")
    async def preview(body: CampaignIn):
        record = draft(body.draft_id)
        spec = record["plan"] if body.split == "development" else record["holdout"]
        if not spec:
            raise HTTPException(422, "This draft has no holdout benchmark recipe")
        if len(body.model_ids) != len(set(body.model_ids)):
            raise HTTPException(422, "Select each model only once")
        selected = {ident: checked(models.get, ident) for ident in body.model_ids}
        prepared = checked(
            prepare,
            spec,
            selected,
            body.baselines,
            body.interventions,
            body.name,
            body.parallelism,
            body.mode,
            body.stopping,
            body.profiles,
        )
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
    async def launch(body: StartIn):
        prepared = checked(read_json, folder("previews", body.preview_id) / "preview.json")
        prepared["id"] = uuid.uuid4().hex[:24]
        checked(runner.launch, prepared)
        return public(prepared)

    @router.get("/evaluations")
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
    def read_campaign(ident: str):
        return public(campaign(ident))

    @router.post("/evaluations/{ident}/pause")
    async def pause(ident: str):
        await runner.pause(campaign(ident))
        return public(campaign(ident))

    @router.post("/evaluations/{ident}/resume")
    async def resume(ident: str):
        record = campaign(ident)
        if record["status"] in {"completed", "completed_with_failures", "failed"}:
            raise HTTPException(
                409, "Create a new evaluation to repeat a finished or failed experiment"
            )
        checked(runner.launch, record)
        return public(record)

    @router.get("/evaluations/{ident}/export")
    def export_report(ident: str):
        record = campaign(ident)
        output = folder("campaigns", ident) / "findings.json"
        checked(write_json, output, dict(public(record), recipe=record["plan"]))
        return FileResponse(output, filename=ident + "-findings.json")

    @router.get("/evaluations/{ident}/smoke-comparison")
    def smoke_comparison(ident: str, reference: str | None = None):
        target = campaign(ident)
        candidates = [read_json(p) for p in sorted((root / "campaigns").glob("*/campaign.json"),
                                                  key=lambda p: p.stat().st_mtime, reverse=True)]
        sources = [s for s in candidates if compatible(s, target)
                   and any(t["status"] == "completed" and t["system_id"].startswith("baseline/")
                           for t in s["studies"])]
        choices = [dict(id=s["id"], name=s["name"]) for s in sources]
        if reference:
            selected = campaign(reference)
        else:
            selected = sources[0] if sources else None
        if selected is None:
            return dict(sources=choices, rows=[], note="No compatible smoke run with completed baselines. "
                        "Run smoke on this draft and split before comparing model results.")
        result = checked(compare_smoke, selected, target, root / "campaigns")
        return dict(result, sources=choices)

    @router.get("/evaluations/{ident}/metrics")
    def metrics(ident: str):
        """Metric debugger: means per system, effective weights, loophole flags, examples."""
        record = campaign(ident)
        done = sum(s["status"] == "completed" for s in record["studies"])
        cache = folder("campaigns", ident) / "metrics.json"
        if cache.is_file():
            saved = read_json(cache)
            if saved["completed_studies"] == done:
                return saved
        report = dict(
            completed_studies=done, metrics=checked(metric_report, record, folder("campaigns", ident))
        )
        if record["status"] in {"completed", "completed_with_failures"}:
            checked(write_json, cache, report)
        return report

    def probe_reports(record):
        return dict(scope=record.get("mode", "unknown"), models={
            s["system_id"]: dict(score=s["report"]["usi"], model_hash=s["model_hash"])
            for s in record["studies"]
            if (record.get("mode") == "full" and s["status"] == "completed"
                and s["intervention"] is None and s.get("report") and s.get("model_hash"))
        })

    @router.get("/evaluations/{ident}/probe-screens/{model_id}")
    def screen_details(ident: str, model_id: str, offset: int = Query(0, ge=0)):
        campaign(ident)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", model_id):
            raise HTTPException(404, "Unknown model screen")
        record = checked(read_json, probes.folder(ident) / f"screen-{model_id}.json")
        bank = probes.bank(ident)
        if not bank or record.get("bank_id") != bank.get("id"):
            raise HTTPException(422, "This screen belongs to an older probe bank; run it again")
        lookup = {p["id"]: p for p in bank["probes"]}
        rows = []
        for row in record.get("results", [])[offset:offset + 1]:
            probe = lookup[row["id"]]
            rows.append(dict(row, tick=probe["tick"], seed=probe["seed"], role=probe["role"], reference_decisions=probe.get("references", {}),
                             oracle_decision=probe["oracle"]))
        return dict(model_id=model_id, offset=offset, total=len(record.get("results", [])), rows=rows)

    @router.get("/evaluations/{ident}/probes")
    def probe_status(ident: str):
        record = campaign(ident)
        status = probes.status(ident)
        reports = probe_reports(record)
        for row in status["screens"]:
            row["comparison_scope"] = reports["scope"]
            row["correlation"] = probes.correlation(ident, reports, row) if not row["stale"] else None
        return status

    @router.post("/evaluations/{ident}/probes")
    async def harvest_probes(ident: str):
        record = campaign(ident)
        bank = checked(harvest, record, folder("campaigns", ident))
        for probe in bank["probes"]:
            probe["references"] = await observable_references(probe)
        bank["id"] = digest({k: v for k, v in bank.items() if k != "id"})
        checked(probes.save_bank, bank)
        return probes.status(ident)

    @router.post("/evaluations/{ident}/probe-screens")
    async def start_screen(ident: str, body: ScreenIn):
        record = campaign(ident)
        if len(body.model_ids) != len(set(body.model_ids)):
            raise HTTPException(422, "Select each model only once")
        selected = {model_id: checked(models.get, model_id) for model_id in body.model_ids}
        reports = probe_reports(record)
        checked(probes.launch, ident, selected, reports, body.parallelism)
        return probes.status(ident)

    add_explorer_routes(router, campaign, folder, runner)

    async def shutdown():
        await runner.shutdown()
        await probes.shutdown()

    router.shutdown_tasks = shutdown
    router.runner = runner
    return router
