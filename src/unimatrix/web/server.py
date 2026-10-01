"""Local benchmark dashboard."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .benchmark import build_router
from .research import build_research_router
from .security import DashboardSecurity


def build_app(
    runs_dir="runs/benchmarks",
    models_dir="config/models",
    recipes_dir="config/recipes",
    default_recipe="standard-v1",
    *,
    allowed_hosts=("localhost", "127.0.0.1", "::1"),
    auth_token=None,
):
    router = build_router(runs_dir, models_dir, recipes_dir, default_recipe)

    research = build_research_router(runs_dir, models_dir, recipes_dir, default_recipe)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await router.shutdown_tasks()
        await research.shutdown_tasks()

    app = FastAPI(title="UNIMATRIx", lifespan=lifespan)
    app.add_middleware(DashboardSecurity, allowed_hosts=allowed_hosts, token=auth_token)
    static = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static), name="static")
    app.include_router(router)
    app.include_router(research)

    @app.get("/")
    def index():
        return FileResponse(static / "benchmark.html")

    @app.get("/recipeManager")
    def recipe_manager():
        return FileResponse(static / "recipe-manager.html")

    @app.get("/recipe-lab")
    def recipe_lab():
        return FileResponse(static / "recipe-lab.html")

    @app.get("/recipe-lab/evaluations/{ident}")
    def evaluation_explorer(ident: str):
        return FileResponse(static / "evaluation-explorer.html")

    return app
