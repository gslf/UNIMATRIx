"""Local dashboard: home, Society Lab, run reports and their APIs."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import build_models_router
from .polis import build_polis_router
from .security import DashboardSecurity


def build_app(
    runs_dir="runs",
    models_dir="config/models",
    societies_dir="config/societies",
    explorer_dir=None,
    *,
    allowed_hosts=("localhost", "127.0.0.1", "::1"),
    auth_token=None,
):
    models = build_models_router(models_dir)
    polis = build_polis_router(runs_dir, models_dir, societies_dir, explorer_dir)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await polis.shutdown_tasks()

    app = FastAPI(title="UNIMATRIx", lifespan=lifespan)
    app.add_middleware(DashboardSecurity, allowed_hosts=allowed_hosts, token=auth_token)
    static = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static), name="static")
    app.include_router(models)
    app.include_router(polis)

    @app.get("/")
    def index():
        return FileResponse(static / "home.html")

    @app.get("/recipe-lab")
    def recipe_lab():
        return FileResponse(static / "society-lab.html")

    @app.get("/society/runs/{ident}")
    def society_report(ident: str):
        return FileResponse(static / "society-report.html")

    return app
