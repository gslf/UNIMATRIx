"""FastAPI control server.

Endpoints per spec section 12. The graph renderer is invoked synchronously on
each /graphs/<name>.png request — cheap enough since rendering happens at most
on user demand. Run deletion is performed on the registry + filesystem; the
caller should ensure the run isn't currently active.
"""
from __future__ import annotations

import asyncio
import io
import json
import shutil
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ..graphs import GRAPH_NAMES, GraphRenderer
from ..orchestrator import Orchestrator
from ..persistence import Registry, RunStore


class BroadcastIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


def build_app(
    orch: Orchestrator,
    registry: Registry,
    runs_dir: Path,
    static_dir: Path,
) -> FastAPI:
    app = FastAPI(title="Unimatrix Control")

    graph_out = orch.run_dir / "graphs"
    graph_out.mkdir(parents=True, exist_ok=True)
    renderer = GraphRenderer(orch.cfg, orch.store, graph_out)

    @contextmanager
    def open_run_store(run_id: int) -> Iterator[RunStore]:
        """Yield a RunStore for the requested run.

        For the live run, reuse the orchestrator's already-open connection so
        we don't fight it for SQLite locks. For other runs, open a fresh
        connection (WAL mode keeps reads non-blocking even if the original
        process is still writing) and close it when done.
        """
        if run_id == orch.run_id:
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

    def _agents_map(store: RunStore) -> dict[str, dict]:
        return {
            a["agent_id"]: {
                "id": a["agent_id"],
                "name": a["name"] or a["agent_id"],
                "role": a.get("role"),
                "class": a.get("class"),
            }
            for a in store.list_agents()
        }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html = (static_dir / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(
            html,
            headers={
                "cache-control": "no-store, no-cache, must-revalidate, max-age=0",
                "pragma": "no-cache",
            },
        )

    @app.get("/state")
    async def state() -> JSONResponse:
        snap = orch.snapshot()
        snap["recent_events"] = await asyncio.to_thread(orch.store.recent_events, 12)
        return JSONResponse(snap)

    @app.get("/diag")
    async def diag() -> JSONResponse:
        return JSONResponse(orch.diagnostics())

    @app.post("/broadcast")
    async def broadcast(body: BroadcastIn) -> JSONResponse:
        status = await orch.queue_broadcast("HUMAN", body.message)
        return JSONResponse({"status": status, "queue_length": orch.snapshot()["broadcast_queue_length"]})

    @app.post("/pause")
    async def pause() -> JSONResponse:
        orch.pause()
        return JSONResponse({"paused": True})

    @app.post("/resume")
    async def resume() -> JSONResponse:
        orch.resume()
        return JSONResponse({"paused": False})

    @app.post("/checkpoint")
    async def checkpoint() -> JSONResponse:
        cid = await orch.force_checkpoint()
        return JSONResponse({"checkpoint_id": cid})

    @app.get("/graphs/{name}.png")
    async def graph(name: str) -> Response:
        if name not in GRAPH_NAMES:
            raise HTTPException(404, f"unknown graph {name}")
        path = await asyncio.to_thread(renderer.render, name)
        return FileResponse(path, media_type="image/png")

    @app.post("/graphs/refresh")
    async def refresh_all() -> JSONResponse:
        results = await asyncio.to_thread(renderer.render_all)
        return JSONResponse({k: str(v) for k, v in results.items()})

    @app.get("/agents")
    async def live_agents() -> JSONResponse:
        """Live-run roster used by the agent explorer panel."""
        def _impl() -> list[dict]:
            agents = orch.store.list_agents()
            return [
                {
                    "id": a["agent_id"],
                    "name": a["name"] or a["agent_id"],
                    "role": a.get("role"),
                    "class": a.get("class"),
                    "state": a.get("state"),
                    "social_need": a.get("social_need"),
                }
                for a in agents
            ]
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/agents/{agent_id}")
    async def live_agent_detail(agent_id: str) -> JSONResponse:
        """Live-run agent profile + compact memory + recent decisions."""
        roles_by_id = {r.id: r for r in orch.cfg.roles}

        def _impl() -> dict:
            a = orch.store.get_agent(agent_id)
            if a is None:
                raise HTTPException(404, f"agent {agent_id} not found")
            try:
                personality = json.loads(a.get("personality") or "{}")
            except json.JSONDecodeError:
                personality = {}
            try:
                values = json.loads(a.get("values_json") or "{}")
            except json.JSONDecodeError:
                values = {}
            role_id = a.get("role")
            role_spec = roles_by_id.get(role_id)
            initial_opinions: dict = {}
            cfg_agent = next(
                (s for s in orch.cfg.agents if s.id == agent_id), None
            )
            if cfg_agent is not None:
                initial_opinions = dict(cfg_agent.initial_opinions or {})

            summaries = orch.store.recent_summaries(
                agent_id, orch.cfg.memory.medium_term_summaries
            )
            impressions = orch.store.list_person_memories_for(agent_id)
            agents_map = _agents_map(orch.store)
            for imp in impressions:
                imp["subject_name"] = agents_map.get(
                    imp["subject_id"], {"name": imp["subject_id"]}
                )["name"]

            decisions = orch.store.agent_decision_history(agent_id, limit=15)
            for d in decisions:
                if d["kind"] == "started_conversation":
                    d["participant_names"] = [
                        agents_map.get(p, {"name": p})["name"]
                        for p in d.get("participants", [])
                    ]
                elif d["kind"] == "vote_proposed":
                    d["target_name"] = agents_map.get(
                        d.get("target_id"), {"name": d.get("target_id")}
                    )["name"]

            votes_raw = orch.store.get_votes_by_voter(agent_id, limit=10)
            recent_votes: list[dict] = []
            for v in votes_raw:
                status = (
                    "malformed"
                    if v.get("reasoning") == "malformed response"
                    else (v.get("vote") or "unknown")
                )
                tgt_id = v.get("target_id")
                recent_votes.append({
                    "proposal_id": v["proposal_id"],
                    "status": status,
                    "vote": v.get("vote"),
                    "reasoning": v.get("reasoning"),
                    "voted_at": v.get("voted_at"),
                    "target_id": tgt_id,
                    "target_name": agents_map.get(
                        tgt_id, {"name": tgt_id}
                    )["name"],
                    "change_type": v.get("change_type"),
                    "from_value": v.get("from_value"),
                    "to_value": v.get("to_value"),
                    "outcome": v.get("outcome"),
                })

            return {
                "id": a["agent_id"],
                "name": a.get("name") or a["agent_id"],
                "gender": a.get("gender"),
                "role": role_id,
                "role_label": role_spec.name if role_spec else role_id,
                "role_prestige": role_spec.prestige if role_spec else None,
                "class": a.get("class"),
                "personality": personality,
                "values": values,
                "backstory": a.get("backstory") or "",
                "initial_opinions": initial_opinions,
                "social_need": a.get("social_need"),
                "state": a.get("state"),
                "current_conversation_id": a.get("current_conversation_id"),
                "memory": {
                    "summaries": summaries,
                    "impressions": impressions,
                },
                "recent_decisions": decisions,
                "recent_votes": recent_votes,
            }

        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs")
    async def list_runs() -> JSONResponse:
        return JSONResponse(registry.list_runs())

    @app.get("/runs/{run_id}/agents")
    async def run_agents(run_id: int) -> JSONResponse:
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                return _agents_map(s)
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/conversations")
    async def run_conversations(run_id: int, limit: int = 500) -> JSONResponse:
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                convs = s.list_conversations(limit=limit)
                agents = _agents_map(s)
                # Decorate with display names so the UI doesn't need a join.
                for c in convs:
                    c["participant_names"] = [
                        agents.get(p, {"name": p})["name"] for p in c["participants"]
                    ]
                    c["initiator_name"] = agents.get(
                        c["initiator_id"], {"name": c["initiator_id"]}
                    )["name"]
                return {"conversations": convs, "agents": agents}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/conversations/{conv_id}")
    async def run_conversation_detail(run_id: int, conv_id: int) -> JSONResponse:
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                conv = s.get_conversation(conv_id)
                if conv is None:
                    raise HTTPException(404, f"conversation {conv_id} not found")
                try:
                    conv["participants"] = json.loads(conv["participants"])
                except (TypeError, json.JSONDecodeError):
                    conv["participants"] = []
                msgs = s.get_messages(conv_id)
                agents = _agents_map(s)
                for m in msgs:
                    m["sender_name"] = agents.get(
                        m["sender_id"], {"name": m["sender_id"]}
                    )["name"]
                conv["participant_names"] = [
                    agents.get(p, {"name": p})["name"] for p in conv["participants"]
                ]
                return {"conversation": conv, "messages": msgs}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/events")
    async def run_events(run_id: int, limit: int = 100) -> JSONResponse:
        def _impl() -> list[dict]:
            with open_run_store(run_id) as s:
                return s.recent_events(limit)
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/votes")
    async def run_votes(run_id: int) -> JSONResponse:
        """List of vote proposals (newest first) decorated with names."""
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                proposals = s.list_proposals()
                proposals.sort(key=lambda p: p["id"], reverse=True)
                agents = _agents_map(s)
                for p in proposals:
                    p["proposer_name"] = agents.get(
                        p.get("proposer_id"), {"name": p.get("proposer_id")}
                    )["name"]
                    p["target_name"] = agents.get(
                        p.get("target_id"), {"name": p.get("target_id")}
                    )["name"]
                return {"proposals": proposals, "agents": agents}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/votes/{proposal_id}")
    async def run_vote_detail(run_id: int, proposal_id: int) -> JSONResponse:
        """Proposal + pre-vote debate transcript + individual votes."""
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                proposal = s.get_proposal(proposal_id)
                if proposal is None:
                    raise HTTPException(
                        404, f"proposal {proposal_id} not found"
                    )
                agents = _agents_map(s)
                proposal["proposer_name"] = agents.get(
                    proposal.get("proposer_id"),
                    {"name": proposal.get("proposer_id")},
                )["name"]
                proposal["target_name"] = agents.get(
                    proposal.get("target_id"),
                    {"name": proposal.get("target_id")},
                )["name"]
                debate = s.debate_speeches_for(proposal_id)
                votes = s.get_votes(proposal_id)
                for v in votes:
                    v["voter_name"] = agents.get(
                        v.get("voter_id"), {"name": v.get("voter_id")}
                    )["name"]
                    v["status"] = (
                        "malformed"
                        if v.get("reasoning") == "malformed response"
                        else (v.get("vote") or "unknown")
                    )
                votes.sort(key=lambda v: (
                    0 if v["status"] == "yes"
                    else 1 if v["status"] == "no"
                    else 2,
                    v.get("voter_id") or "",
                ))
                return {
                    "proposal": proposal,
                    "debate": debate,
                    "votes": votes,
                }
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/broadcasts")
    async def run_broadcasts(run_id: int, limit: int = 500) -> JSONResponse:
        """List of broadcast events (human + agent) newest first."""
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                events = s.events_by_type(
                    ["broadcast_agent", "broadcast_human"], limit=limit
                )
                agents = _agents_map(s)
                broadcasts: list[dict] = []
                for e in events:
                    payload = e.get("payload") or {}
                    sender_id = payload.get("sender_id") or ""
                    sender_name = (
                        "(human / public crier)"
                        if sender_id == "HUMAN"
                        else agents.get(
                            sender_id, {"name": sender_id}
                        )["name"]
                    )
                    broadcasts.append({
                        "id": e.get("id"),
                        "ts": e.get("ts"),
                        "event_type": e.get("event_type"),
                        "sender_id": sender_id,
                        "sender_name": sender_name,
                        "message": payload.get("message") or "",
                    })
                return {"broadcasts": broadcasts, "agents": agents}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.delete("/runs/{run_id}")
    async def delete_run(run_id: int) -> JSONResponse:
        info = registry.get(run_id)
        if info is None:
            raise HTTPException(404, f"run {run_id} not found")
        if run_id == orch.run_id:
            raise HTTPException(409, "cannot delete the currently running run")
        # remove db file
        db_path = Path(info["db_path"])
        if db_path.exists():
            try:
                db_path.unlink()
            except OSError:
                pass
            for ext in ("-wal", "-shm", "-journal"):
                p = db_path.with_name(db_path.name + ext)
                if p.exists():
                    p.unlink(missing_ok=True)
        # remove the run's directory (graphs, chroma)
        run_subdir = runs_dir / db_path.stem
        if run_subdir.exists():
            shutil.rmtree(run_subdir, ignore_errors=True)
        registry.delete(run_id)
        return JSONResponse({"deleted": run_id})

    @app.get("/runs/{run_id}/export")
    async def export_run(run_id: int) -> Response:
        info = registry.get(run_id)
        if info is None:
            raise HTTPException(404, f"run {run_id} not found")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("config.json", info["config_json"])
            zf.writestr("registry.json", json.dumps(info, indent=2))
            db_path = Path(info["db_path"])
            if db_path.exists():
                zf.write(db_path, arcname=db_path.name)
            run_subdir = runs_dir / db_path.stem
            if run_subdir.exists():
                for p in run_subdir.rglob("*"):
                    if p.is_file():
                        zf.write(p, arcname=str(p.relative_to(runs_dir)))
        buf.seek(0)
        return Response(
            content=buf.read(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="run_{run_id}.zip"'
            },
        )

    return app
