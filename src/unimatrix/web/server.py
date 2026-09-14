"""Local benchmark dashboard."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .benchmark import build_router
from .research import build_research_router


def build_app(
    runs_dir="runs/benchmarks",
    models_dir="config/models",
    recipes_dir="config/recipes",
    default_recipe="standard-v1",
    **legacy,
):
    recipes_dir = legacy.pop("plans_dir", recipes_dir)
    default_recipe = legacy.pop("default_plan", default_recipe)
    if legacy:
        raise TypeError("Unknown server options: " + ", ".join(legacy))
    router = build_router(runs_dir, models_dir, recipes_dir, default_recipe)

    research = build_research_router(runs_dir, models_dir, recipes_dir, default_recipe)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await router.shutdown_tasks()
        await research.shutdown_tasks()

    app = FastAPI(title="UNIMATRIx", lifespan=lifespan)
    static = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static), name="static")
    app.include_router(router)
    app.include_router(research)
    app.include_router(research.legacy_router, include_in_schema=False)

    @app.get("/")
    def index():
        return FileResponse(static / "benchmark.html")

    @app.get("/recipe-lab")
    def recipe_lab():
        return FileResponse(static / "recipe-lab.html")

    @app.get("/recipe-lab/evaluations/{ident}")
    def evaluation_explorer(ident: str):
        return FileResponse(static / "evaluation-explorer.html")

    @app.get("/research", include_in_schema=False)
    def legacy_research_page():
        return RedirectResponse("/recipe-lab", status_code=307)

    return app
