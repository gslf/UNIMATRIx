"""FastAPI control server.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ..orchestrator import Orchestrator
from ..persistence import Registry, RunStore, utc_now_iso
from ..session import SessionError, SessionManager


class BroadcastIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class StartIn(BaseModel):
    config: str = Field(..., min_length=1, max_length=200)


def _idle_state(session: SessionManager) -> dict:
    """State payload returned when no simulation is running."""
    recent_lines = getattr(session.console, "recent_log_lines", None)
    events = recent_lines(1000) if callable(recent_lines) else []
    return {
        "running": False, "run_id": None, "paused": False, "tick_no": 0,
        "population": {"alive": 0, "dead": 0}, "recent_messages": [],
        "broadcast_queue_length": 0, "metrics": {},
        "recent_events": events, "configs": session.list_configs(),
    }


def _parse_json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def build_app(
    session: SessionManager,
    registry: Registry,
    runs_dir: Path,
    static_dir: Path,
) -> FastAPI:
    app = FastAPI(title="Unimatrix")

    def _require_orch() -> Orchestrator:
        orch = session.orchestrator
        if orch is None or not session.is_active:
            raise HTTPException(409, "no active simulation")
        return orch

    @contextmanager
    def open_run_store(run_id: int) -> Iterator[RunStore]:
        """Yield a RunStore for the requested run — the live orchestrator's open
        connection for the active run, else a fresh (WAL) read connection."""
        orch = session.orchestrator
        if orch is not None and run_id == orch.run_id:
            yield orch.store
            return
        info = registry.get(run_id)
        if info is None:
            raise HTTPException(404, f"run {run_id} not found")
        db_path = Path(info["db_path"])
        if not db_path.exists():
            raise HTTPException(410, f"run {run_id} db file missing at {db_path}")
        store = RunStore(db_path)
        try:
            yield store
        finally:
            store.close()

    # ----- the control panel -----

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(
            (static_dir / "index.html").read_text(encoding="utf-8"),
            headers={"cache-control": "no-store, no-cache, must-revalidate, max-age=0"},
        )

    # ----- simulation lifecycle -----

    @app.get("/configs")
    async def list_available_configs() -> JSONResponse:
        return JSONResponse({"configs": session.list_configs()})

    @app.post("/sim/start")
    async def sim_start(body: StartIn) -> JSONResponse:
        try:
            run_id = await session.start(body.config)
        except SessionError as exc:
            raise HTTPException(exc.status, exc.message)
        return JSONResponse({"running": True, "run_id": run_id})

    @app.post("/sim/stop")
    async def sim_stop() -> JSONResponse:
        return JSONResponse({"running": False, "stopped": await session.stop()})

    @app.get("/state")
    async def state() -> JSONResponse:
        orch = session.orchestrator
        if orch is None or not session.is_active:
            return JSONResponse(_idle_state(session))
        snap = orch.snapshot()
        snap["running"] = True
        snap["configs"] = session.list_configs()
        recent_lines = getattr(orch.console, "recent_log_lines", None)
        snap["recent_events"] = (
            recent_lines(1000) if callable(recent_lines)
            else await asyncio.to_thread(orch.store.recent_events, 1000)
        )
        return JSONResponse(snap)

    @app.get("/diag")
    async def diag() -> JSONResponse:
        return JSONResponse(_require_orch().diagnostics())

    @app.post("/broadcast")
    async def broadcast(body: BroadcastIn) -> JSONResponse:
        orch = _require_orch()
        status = await orch.queue_broadcast("HUMAN", body.message)
        return JSONResponse(
            {"status": status, "queue_length": orch.snapshot()["broadcast_queue_length"]}
        )

    @app.post("/pause")
    async def pause() -> JSONResponse:
        _require_orch().pause()
        return JSONResponse({"paused": True})

    @app.post("/resume")
    async def resume() -> JSONResponse:
        _require_orch().resume()
        return JSONResponse({"paused": False})

    @app.post("/checkpoint")
    async def checkpoint() -> JSONResponse:
        cid = await _require_orch().force_checkpoint()
        return JSONResponse({"checkpoint_id": cid})

    # ----- run index -----

    @app.get("/runs")
    async def list_runs() -> JSONResponse:
        return JSONResponse(registry.list_runs())

    @app.post("/runs/{run_id}/resume")
    async def runs_resume(run_id: int) -> JSONResponse:
        try:
            await session.resume(run_id)
        except SessionError as exc:
            raise HTTPException(exc.status, exc.message)
        return JSONResponse({"running": True, "run_id": run_id})

    @app.post("/runs/{run_id}/end")
    async def runs_end(run_id: int) -> JSONResponse:
        info = registry.get(run_id)
        if info is None:
            raise HTTPException(404, f"run {run_id} not found")
        orch = session.orchestrator
        if orch is not None and run_id == orch.run_id and session.is_active:
            raise HTTPException(409, "this run is currently live; use /sim/stop")
        registry.update_status(run_id, "ended", utc_now_iso())
        return JSONResponse({"ended": True})

    @app.delete("/runs/{run_id}")
    async def delete_run(run_id: int) -> JSONResponse:
        info = registry.get(run_id)
        if info is None:
            raise HTTPException(404, f"run {run_id} not found")
        orch = session.orchestrator
        if orch is not None and run_id == orch.run_id and session.is_active:
            raise HTTPException(409, "cannot delete the currently running run")
        db_path = Path(info["db_path"])
        for p in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        subdir = runs_dir / db_path.stem
        if subdir.is_dir():
            shutil.rmtree(subdir, ignore_errors=True)
        registry.delete(run_id)
        return JSONResponse({"deleted": True})

    # ----- way of life: read-only data per run -----

    def _names(s: RunStore) -> dict:
        return {a["agent_id"]: a.get("name") for a in s.list_agents()}

    def _labor_summary(s: RunStore, recent_n: int = 14) -> dict:
        """Aggregate every `harvest` event into the labor picture the World tab
        shows: totals, who worked how much, per-patch draw, and recent sessions.
        `harvest` payloads carry {patch, harvesters, drawn, shares:{id:amt}, tick}."""
        evs = s.events_by_type("harvest", 20000)  # oldest-first
        sessions = cooperative = 0
        total_drawn = 0.0
        per_being: dict[str, dict] = {}
        by_patch: dict[str, float] = {}
        for e in evs:
            p = e.get("payload") or {}
            crew = p.get("harvesters") or []
            drawn = float(p.get("drawn") or 0)
            sessions += 1
            cooperative += 1 if len(crew) > 1 else 0
            total_drawn += drawn
            if p.get("patch"):
                by_patch[p["patch"]] = round(by_patch.get(p["patch"], 0.0) + drawn, 1)
            shares = p.get("shares") or {}
            if not shares and crew:  # older events without per-head shares
                shares = {aid: drawn / len(crew) for aid in crew}
            for aid, amt in shares.items():
                d = per_being.setdefault(aid, {"sessions": 0, "drawn": 0.0})
                d["sessions"] += 1
                d["drawn"] += float(amt or 0)
        for d in per_being.values():
            d["drawn"] = round(d["drawn"], 1)
        recent = [{
            "patch": (e.get("payload") or {}).get("patch"),
            "harvesters": (e.get("payload") or {}).get("harvesters") or [],
            "drawn": (e.get("payload") or {}).get("drawn"),
            "tick": (e.get("payload") or {}).get("tick"),
            "cooperative": len((e.get("payload") or {}).get("harvesters") or []) > 1,
        } for e in reversed(evs[-recent_n:])]  # newest first
        return {
            "sessions": sessions, "cooperative": cooperative,
            "solo": sessions - cooperative, "total_drawn": round(total_drawn, 1),
            "per_being": per_being, "by_patch": by_patch, "recent": recent,
        }

    @app.get("/runs/{run_id}/beings")
    async def run_beings(run_id: int) -> JSONResponse:
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                beings = [{
                    "id": a["agent_id"], "name": a.get("name") or a["agent_id"],
                    "gender": a.get("gender"), "alive": bool(a.get("alive", 1)),
                    "vitality": a.get("vitality"), "sustenance": a.get("sustenance"),
                    "self_model_version": a.get("self_model_version") or 0,
                    "self_model": _parse_json(a.get("self_model_json"), {}),
                    "born_tick": a.get("born_tick") or 0,
                    "parent_ids": _parse_json(a.get("parent_ids"), []),
                    "traits": _parse_json(a.get("traits_json"), {}),
                    "pos_x": a.get("pos_x") or 0,
                    "pos_y": a.get("pos_y") or 0,
                    "circumstance": a.get("circumstance"),
                    "disposition": a.get("disposition"),
                } for a in s.list_agents()]
                return {"beings": beings}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/space")
    async def run_space(run_id: int) -> JSONResponse:
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                patches = s.list_patches()
                beings = [{
                    "id": a["agent_id"], "name": a.get("name") or a["agent_id"],
                    "alive": bool(a.get("alive", 1)),
                    "vitality": a.get("vitality"),
                    "pos_x": a.get("pos_x") or 0, "pos_y": a.get("pos_y") or 0,
                } for a in s.list_agents() if bool(a.get("alive", 1))]
                return {"patches": patches, "beings": beings,
                        "labor": _labor_summary(s), "names": _names(s)}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/self/{agent_id}")
    async def run_self(run_id: int, agent_id: str) -> JSONResponse:
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                ag = s.get_agent(agent_id)
                return {"agent_id": agent_id,
                        "name": (ag or {}).get("name") or agent_id,
                        "versions": s.list_self_models(agent_id)}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/meaning")
    async def run_meaning(run_id: int) -> JSONResponse:
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                names = _names(s)
                arts = s.list_artifacts(2000)
                for a in arts:
                    a["author_name"] = names.get(a.get("author_id"))
                return {"artifacts": arts, "adoptions": s.list_adoptions(),
                        "names": names}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/kinship")
    async def run_kinship(run_id: int) -> JSONResponse:
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                return {"relationships": s.list_relationships(),
                        "names": _names(s)}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/continuity")
    async def run_continuity(run_id: int) -> JSONResponse:
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                return {"deaths": s.list_deaths(), "lineage": s.list_lineage(),
                        "names": _names(s)}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/timeline")
    async def run_timeline(run_id: int) -> JSONResponse:
        def _ticks(rows, key):
            return [int(r[key]) for r in rows if r.get(key) is not None]

        def _impl() -> dict:
            with open_run_store(run_id) as s:
                agents = s.list_agents()
                # Split each harvest session by tick into all / cooperative / solo,
                # so the panel can chart whether beings come to work together.
                work_sessions, coop_sessions, solo_sessions = [], [], []
                for e in s.events_by_type("harvest", 20000):
                    p = e.get("payload") or {}
                    t = p.get("tick")
                    if t is None:
                        continue
                    t = int(t)
                    work_sessions.append(t)
                    (coop_sessions if len(p.get("harvesters") or []) > 1
                     else solo_sessions).append(t)
                return {
                    "founders": sum(1 for a in agents if not (a.get("born_tick") or 0)),
                    "self_revisions": s.self_model_ticks(),
                    "artifacts": _ticks(s.list_artifacts(5000), "tick_no"),
                    "adoptions": _ticks(s.list_adoptions(), "tick_no"),
                    "work_sessions": work_sessions,
                    "cooperative_sessions": coop_sessions,
                    "solo_sessions": solo_sessions,
                    "bonds": _ticks(s.list_relationships(), "formed_tick"),
                    "births": _ticks(s.list_lineage(), "tick_no"),
                    "deaths": _ticks(s.list_deaths(), "tick_no"),
                }
        return JSONResponse(await asyncio.to_thread(_impl))

    return app
