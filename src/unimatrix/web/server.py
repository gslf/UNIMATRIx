"""FastAPI control server.
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ..config import Config, strip_removed_legacy_keys
from ..graphs import GRAPH_NAMES, GraphRenderer, GraphUnavailable
from ..orchestrator import Orchestrator
from ..persistence import Registry, RunStore, utc_now_iso
from ..session import SessionError, SessionManager


class BroadcastIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class StartIn(BaseModel):
    config: str = Field(..., min_length=1, max_length=200)


def _config_for_run(registry: Registry, run_id: int) -> Config:
    info = registry.get(run_id)
    if info is None:
        raise HTTPException(404, f"run {run_id} not found")
    try:
        return Config.model_validate(
            strip_removed_legacy_keys(json.loads(info["config_json"]))
        )
    except Exception as exc:
        raise HTTPException(
            500, f"saved config for run {run_id} is invalid: {exc}"
        )


def _status_movements(
    store: RunStore, agent_id: str, roles_by_id: dict, cfg: Config
) -> dict:
    """Per-agent role & class movement timelines plus the starting prestige and
    popularity derived from the agent's initial role/class.

    The `status_changes` table tags each row 'role', 'class' or 'initial'. The
    'initial' baseline rows carry both a role and a class (indistinguishable by
    `change_type`), so we split them by whether `to_value` is a known role id or
    class id. Starting prestige/popularity are the same values the bootstrap
    seeds from (nominal role prestige; the class's popularity floor).
    """
    thresholds = cfg.resolved_class_thresholds()
    class_ids = set(cfg.classes)
    role_ids = set(roles_by_id)
    role_timeline: list[dict] = []
    class_timeline: list[dict] = []
    for c in store.list_status_changes_for(agent_id):  # oldest first
        ct = c["change_type"]
        to_val = c["to_value"]
        entry = {"ts": c["ts"], "from": c["from_value"], "to": to_val}
        if ct == "role" or (ct == "initial" and to_val in role_ids):
            spec = roles_by_id.get(to_val)
            entry["to_label"] = spec.name if spec else to_val
            role_timeline.append(entry)
        elif ct == "class" or (ct == "initial" and to_val in class_ids):
            class_timeline.append(entry)

    starting_role = role_timeline[0]["to"] if role_timeline else None
    starting_class = class_timeline[0]["to"] if class_timeline else None
    role_spec = roles_by_id.get(starting_role)
    t = thresholds.get(starting_class)
    return {
        "role_timeline": role_timeline,
        "class_timeline": class_timeline,
        "starting_role": starting_role,
        "starting_role_label": role_spec.name if role_spec else starting_role,
        "starting_class": starting_class,
        "starting_prestige": float(role_spec.prestige) if role_spec else None,
        "starting_popularity": float(t.popularity_min) if t else None,
    }


# Elections persist their ballots in `vote_proposals`, one row per office
# ballot (change_type='office') plus one per reassignment (change_type=
# 'reassign'). Ballots from the same election share a tick, embedded in the
# motivation string by ElectionModule, e.g. "election tick 40: head".
_ELECTION_TICK_RE = re.compile(r"election tick (\d+)")


def _proposal_tick(proposal: dict) -> int | None:
    m = _ELECTION_TICK_RE.search(proposal.get("motivation") or "")
    return int(m.group(1)) if m else None


def _office_summary(p: dict, agents: dict, roles_by_id: dict) -> dict:
    """Headline of one office ballot: who won, who held it, whether it flipped.

    `from_value` is the incumbent's id, `to_value` the office role-id,
    `target_id` the winner, and yes/no counts the winner's and runner-up's
    tallies (see ElectionModule._office_ballot)."""
    office = p.get("to_value")
    incumbent_id = p.get("from_value") or None
    winner_id = p.get("target_id") or None
    role = roles_by_id.get(office)
    return {
        "proposal_id": p.get("id"),
        "office": office,
        "office_label": role.name if role else office,
        "incumbent_id": incumbent_id,
        "incumbent_name": agents.get(
            incumbent_id, {"name": incumbent_id}
        )["name"] if incumbent_id else None,
        "winner_id": winner_id,
        "winner_name": agents.get(
            winner_id, {"name": winner_id}
        )["name"] if winner_id else None,
        "changed": bool(winner_id) and winner_id != incumbent_id,
        "winner_votes": p.get("yes_count"),
        "runner_up_votes": p.get("no_count"),
    }


def _reassign_summary(p: dict, agents: dict, roles_by_id: dict) -> dict:
    """Headline of one reassignment ballot: where an unseated officeholder
    (`target_id`, formerly office `from_value`) lands (`to_value`)."""
    new_role = p.get("to_value")
    target_id = p.get("target_id") or None
    role = roles_by_id.get(new_role)
    return {
        "proposal_id": p.get("id"),
        "office": p.get("from_value"),
        "target_id": target_id,
        "target_name": agents.get(
            target_id, {"name": target_id}
        )["name"] if target_id else None,
        "new_role": new_role,
        "new_role_label": role.name if role else new_role,
        "role_votes": p.get("yes_count"),
    }


def _ballot_view(votes: list[dict], agents: dict, name_for) -> dict:
    """Per-candidate tally + sorted individual ballots for one election ballot.

    Each `votes` row carries `vote` = a candidate agent-id / role-id, or
    "abstain" (also covers malformed responses, whose last raw attempt is kept
    in `raw_response`). `name_for` resolves a choice id to its display label
    (agent name for office ballots, role label for reassignments)."""
    tally: Counter = Counter()
    abstain = 0
    rows: list[dict] = []
    for v in votes:
        choice = v.get("vote")
        is_abstain = (not choice) or choice == "abstain"
        if is_abstain:
            abstain += 1
        else:
            tally[choice] += 1
        rows.append({
            "voter_id": v.get("voter_id"),
            "voter_name": agents.get(
                v.get("voter_id"), {"name": v.get("voter_id")}
            )["name"],
            "choice_id": None if is_abstain else choice,
            "is_abstain": is_abstain,
            "raw_response": v.get("raw_response"),
            "voted_at": v.get("voted_at"),
        })
    rank = {cid: i for i, (cid, _) in enumerate(tally.most_common())}
    for row in rows:
        row["choice_name"] = (
            name_for(row["choice_id"]) if row["choice_id"] else None
        )
    # Winner's voters first, then runners-up by tally rank, abstains last.
    rows.sort(key=lambda r: (
        r["is_abstain"],
        rank.get(r["choice_id"], 10 ** 9),
        r["voter_name"] or "",
    ))
    tally_list = [
        {"choice_id": cid, "choice_name": name_for(cid), "count": n}
        for cid, n in tally.most_common()
    ]
    return {"tally": tally_list, "abstain_count": abstain, "votes": rows}


def _group_elections(
    proposals: list[dict],
    agents: dict,
    roles_by_id: dict,
    office_order: dict,
    debate_counts: dict,
) -> list[dict]:
    """Bucket election ballots by tick into election summaries (newest first).
    Proposals with no election tick (e.g. legacy yes/no proposals) are
    ignored — the explorer is elections-only under the new architecture."""
    by_tick: dict[int, list[dict]] = {}
    for p in proposals:
        tick = _proposal_tick(p)
        if tick is None:
            continue
        by_tick.setdefault(tick, []).append(p)
    elections: list[dict] = []
    for tick, ballots in by_tick.items():
        offices = [
            _office_summary(p, agents, roles_by_id)
            for p in ballots if p.get("change_type") == "office"
        ]
        offices.sort(key=lambda o: office_order.get(o["office"], 10 ** 9))
        reassignments = [
            _reassign_summary(p, agents, roles_by_id)
            for p in ballots if p.get("change_type") == "reassign"
        ]
        proposed = [p.get("proposed_at") for p in ballots if p.get("proposed_at")]
        closed = [p.get("closed_at") for p in ballots if p.get("closed_at")]
        elections.append({
            "tick": tick,
            "proposed_at": min(proposed) if proposed else None,
            "closed_at": max(closed) if closed else None,
            "debate_count": debate_counts.get(tick, 0),
            "offices": offices,
            "reassignments": reassignments,
            "changed_count": sum(1 for o in offices if o["changed"]),
        })
    elections.sort(key=lambda e: e["tick"], reverse=True)
    return elections


def _decorate_recent_votes(
    votes_raw: list[dict], agents_map: dict, roles_by_id: dict
) -> list[dict]:
    """Reshape an agent's recent ballots (get_votes_by_voter join) into the
    election-aware shape the agent detail panel renders: which office each
    ballot was for, and who/what the agent picked (or abstain)."""
    out: list[dict] = []
    for v in votes_raw:
        change_type = v.get("change_type")
        choice = v.get("vote")
        is_abstain = (not choice) or choice == "abstain"
        if change_type == "reassign":
            office = v.get("from_value")
            role = roles_by_id.get(choice)
            choice_name = (role.name if role else choice) if not is_abstain else None
        else:  # office ballot (or legacy)
            office = v.get("to_value")
            choice_name = (
                agents_map.get(choice, {"name": choice})["name"]
                if not is_abstain else None
            )
        office_role = roles_by_id.get(office)
        out.append({
            "proposal_id": v["proposal_id"],
            "kind": change_type,
            "office": office,
            "office_label": office_role.name if office_role else office,
            "choice_id": None if is_abstain else choice,
            "choice_name": choice_name,
            "is_abstain": is_abstain,
            "raw_response": v.get("raw_response"),
            "voted_at": v.get("voted_at"),
            "outcome": v.get("outcome"),
        })
    return out


def _idle_state(session: SessionManager) -> dict:
    """State payload returned when no simulation is currently running."""
    recent_lines = getattr(session.console, "recent_log_lines", None)
    events = recent_lines(1000) if callable(recent_lines) else []
    return {
        "running": False,
        "run_id": None,
        "paused": False,
        "tick_no": 0,
        "seconds_since_last_tick": None,
        "seconds_since_last_activity": None,
        "agents_per_state": {},
        "agents_per_class": {},
        "recent_messages": [],
        "active_vote": None,
        "broadcast_queue_length": 0,
        "metrics": {},
        "recent_events": events,
        "configs": session.list_configs(),
    }


def build_app(
    session: SessionManager,
    registry: Registry,
    runs_dir: Path,
    static_dir: Path,
) -> FastAPI:
    app = FastAPI(title="Unimatrix Control")

    def _require_orch() -> Orchestrator:
        orch = session.orchestrator
        if orch is None or not session.is_active:
            raise HTTPException(409, "no active simulation")
        return orch

    def _renderer_for(orch: Orchestrator) -> GraphRenderer:
        graph_out = orch.run_dir / "graphs"
        graph_out.mkdir(parents=True, exist_ok=True)
        return GraphRenderer(orch.cfg, orch.store, graph_out)

    @contextmanager
    def open_run_store(run_id: int) -> Iterator[RunStore]:
        """Yield a RunStore for the requested run.

        For the live run, reuse the orchestrator's already-open connection so
        we don't fight it for SQLite locks. For other runs, open a fresh
        connection (WAL mode keeps reads non-blocking even if the original
        process is still writing) and close it when done.
        """
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

    # ----- session lifecycle -----

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
        stopped = await session.stop()
        return JSONResponse({"running": False, "stopped": stopped})

    # ----- live orchestrator views -----

    @app.get("/state")
    async def state() -> JSONResponse:
        orch = session.orchestrator
        if orch is None or not session.is_active:
            return JSONResponse(_idle_state(session))
        snap = orch.snapshot()
        snap["running"] = True
        snap["configs"] = session.list_configs()
        recent_lines = getattr(orch.console, "recent_log_lines", None)
        if callable(recent_lines):
            snap["recent_events"] = recent_lines(1000)
        else:
            snap["recent_events"] = await asyncio.to_thread(
                orch.store.recent_events, 1000
            )
        return JSONResponse(snap)

    @app.get("/diag")
    async def diag() -> JSONResponse:
        orch = _require_orch()
        return JSONResponse(orch.diagnostics())

    @app.post("/broadcast")
    async def broadcast(body: BroadcastIn) -> JSONResponse:
        orch = _require_orch()
        status = await orch.queue_broadcast("HUMAN", body.message)
        return JSONResponse({
            "status": status,
            "queue_length": orch.snapshot()["broadcast_queue_length"],
        })

    @app.post("/pause")
    async def pause() -> JSONResponse:
        orch = _require_orch()
        orch.pause()
        return JSONResponse({"paused": True})

    @app.post("/resume")
    async def resume() -> JSONResponse:
        orch = _require_orch()
        orch.resume()
        return JSONResponse({"paused": False})

    @app.post("/checkpoint")
    async def checkpoint() -> JSONResponse:
        orch = _require_orch()
        cid = await orch.force_checkpoint()
        return JSONResponse({"checkpoint_id": cid})

    @app.get("/graphs/{name}.png")
    async def graph(name: str) -> Response:
        if name not in GRAPH_NAMES:
            raise HTTPException(404, f"unknown graph {name}")
        orch = _require_orch()
        renderer = _renderer_for(orch)
        try:
            path = await asyncio.to_thread(renderer.render, name)
        except GraphUnavailable:
            raise HTTPException(404, f"graph {name} has no data")
        return FileResponse(path, media_type="image/png")

    @app.post("/graphs/refresh")
    async def refresh_all() -> JSONResponse:
        orch = _require_orch()
        renderer = _renderer_for(orch)
        results = await asyncio.to_thread(renderer.render_all)
        return JSONResponse({k: str(v) for k, v in results.items()})

    @app.get("/agents")
    async def live_agents() -> JSONResponse:
        """Live-run roster used by the agent explorer panel."""
        orch = _require_orch()

        def _impl() -> list[dict]:
            agents = orch.store.list_agents()
            return [
                {
                    "id": a["agent_id"],
                    "name": a["name"] or a["agent_id"],
                    "role": a.get("role"),
                    "class": a.get("class"),
                    "office": a.get("office"),
                    "state": a.get("state"),
                    "social_need": a.get("social_need"),
                    "prestige": a.get("prestige"),
                    "popularity": a.get("popularity"),
                }
                for a in agents
            ]
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/agents/{agent_id}")
    async def live_agent_detail(agent_id: str) -> JSONResponse:
        """Live-run agent profile + compact memory + recent decisions."""
        orch = _require_orch()
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
            opinions: dict = {}
            cfg_agent = next(
                (s for s in orch.cfg.agents if s.id == agent_id), None
            )
            if cfg_agent is not None:
                opinions = dict(cfg_agent.opinions or {})

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
                if d["kind"] == "sent_message":
                    d["recipient_names"] = [
                        agents_map.get(p, {"name": p})["name"]
                        for p in d.get("recipients", [])
                    ]
                elif d["kind"] == "vote_proposed":
                    d["target_name"] = agents_map.get(
                        d.get("target_id"), {"name": d.get("target_id")}
                    )["name"]

            recent_votes = _decorate_recent_votes(
                orch.store.get_votes_by_voter(agent_id, limit=10),
                agents_map, roles_by_id,
            )

            txs = orch.store.list_transactions(limit=30, agent_id=agent_id)
            movements = _status_movements(
                orch.store, agent_id, roles_by_id, orch.cfg
            )
            return {
                "id": a["agent_id"],
                "name": a.get("name") or a["agent_id"],
                "gender": a.get("gender"),
                "role": role_id,
                "role_label": role_spec.name if role_spec else role_id,
                "role_prestige": role_spec.prestige if role_spec else None,
                "class": a.get("class"),
                "office": a.get("office"),
                "personality": personality,
                "values": values,
                "backstory": a.get("backstory") or "",
                "opinions": opinions,
                "social_need": a.get("social_need"),
                "prestige": a.get("prestige"),
                "popularity": a.get("popularity"),
                "state": a.get("state"),
                "bank_account": a.get("bank_account"),
                "recent_transactions": txs,
                "status_movements": movements,
                "memory": {
                    "summaries": summaries,
                    "impressions": impressions,
                },
                "recent_decisions": decisions,
                "recent_votes": recent_votes,
            }

        return JSONResponse(await asyncio.to_thread(_impl))

    # ----- registry / past-run views -----

    @app.get("/runs")
    async def list_runs() -> JSONResponse:
        return JSONResponse(registry.list_runs())

    @app.get("/runs/{run_id}/agents")
    async def run_agents(run_id: int) -> JSONResponse:
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                return _agents_map(s)
        return JSONResponse(await asyncio.to_thread(_impl))

    # ---- past-run "load everything into the GUI" endpoints ----

    @app.post("/runs/{run_id}/resume")
    async def runs_resume(run_id: int) -> JSONResponse:
        try:
            await session.resume(run_id)
        except SessionError as exc:
            raise HTTPException(exc.status, exc.message)
        return JSONResponse({"running": True, "run_id": run_id})

    @app.post("/runs/{run_id}/end")
    async def runs_end(run_id: int) -> JSONResponse:
        """Terminally mark a paused past-run as ended without resuming."""
        info = registry.get(run_id)
        if info is None:
            raise HTTPException(404, f"run {run_id} not found")
        orch = session.orchestrator
        if orch is not None and run_id == orch.run_id and session.is_active:
            raise HTTPException(
                409, "this run is currently live; use /sim/stop instead"
            )
        if (info.get("status") or "") not in ("paused", "running"):
            raise HTTPException(
                409,
                f"run {run_id} is '{info.get('status')}'; only paused/running "
                "runs can be ended manually",
            )
        registry.update_status(run_id, "ended", utc_now_iso())
        return JSONResponse({"run_id": run_id, "status": "ended"})

    @app.get("/runs/{run_id}/state")
    async def run_state(run_id: int) -> JSONResponse:
        """Read-only snapshot synthesized from the past run's DB."""
        info = registry.get(run_id)
        if info is None:
            raise HTTPException(404, f"run {run_id} not found")

        def _impl() -> dict:
            with open_run_store(run_id) as s:
                agents = s.list_agents()
                per_state: dict[str, int] = {}
                per_class: dict[str, int] = {}
                for a in agents:
                    st = a.get("state") or "idle"
                    cl = a.get("class") or "?"
                    per_state[st] = per_state.get(st, 0) + 1
                    per_class[cl] = per_class.get(cl, 0) + 1
                ckpt = s.latest_checkpoint() or {}
                try:
                    tick_no = int(ckpt.get("tick_no") or 0)
                except (TypeError, ValueError):
                    tick_no = 0
                try:
                    community_balance = s.get_community_balance()
                except Exception:
                    community_balance = 0.0
                try:
                    recent_transactions = s.list_transactions(limit=20)
                except Exception:
                    recent_transactions = []
                events = s.recent_events(1000)
            return {
                "run_id": run_id,
                "running": False,
                "paused": (info.get("status") == "paused"),
                "status": info.get("status"),
                "started_at": info.get("started_at"),
                "ended_at": info.get("ended_at"),
                "name": info.get("name"),
                "tick_no": tick_no,
                "seconds_since_last_tick": None,
                "seconds_since_last_activity": None,
                "agents_per_state": per_state,
                "agents_per_class": per_class,
                "recent_messages": [],
                "active_vote": None,
                "broadcast_queue_length": 0,
                "community_balance": round(community_balance, 2),
                "recent_transactions": recent_transactions,
                "recent_events": events,
                "metrics": {},
            }
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/roster")
    async def run_roster(run_id: int) -> JSONResponse:
        """Past-run agent roster shaped like the live `/agents` endpoint."""
        def _impl() -> list[dict]:
            with open_run_store(run_id) as s:
                return [
                    {
                        "id": a["agent_id"],
                        "name": a["name"] or a["agent_id"],
                        "role": a.get("role"),
                        "class": a.get("class"),
                        "office": a.get("office"),
                        "state": a.get("state"),
                        "social_need": a.get("social_need"),
                        "prestige": a.get("prestige"),
                        "popularity": a.get("popularity"),
                    }
                    for a in s.list_agents()
                ]
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/agents/{agent_id}")
    async def run_agent_detail(run_id: int, agent_id: str) -> JSONResponse:
        """Past-run agent profile mirroring the live `/agents/{id}` shape."""
        cfg = _config_for_run(registry, run_id)
        roles_by_id = {r.id: r for r in cfg.roles}
        opinions_by_agent: dict[str, dict] = {
            s.id: dict(s.opinions or {}) for s in cfg.agents
        }

        def _impl() -> dict:
            with open_run_store(run_id) as s:
                a = s.get_agent(agent_id)
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
                opinions = opinions_by_agent.get(agent_id, {})

                summaries = s.recent_summaries(
                    agent_id, cfg.memory.medium_term_summaries
                )
                impressions = s.list_person_memories_for(agent_id)
                agents_map = _agents_map(s)
                for imp in impressions:
                    imp["subject_name"] = agents_map.get(
                        imp["subject_id"], {"name": imp["subject_id"]}
                    )["name"]

                decisions = s.agent_decision_history(agent_id, limit=15)
                for d in decisions:
                    if d["kind"] == "sent_message":
                        d["recipient_names"] = [
                            agents_map.get(p, {"name": p})["name"]
                            for p in d.get("recipients", [])
                        ]
                    elif d["kind"] == "vote_proposed":
                        d["target_name"] = agents_map.get(
                            d.get("target_id"), {"name": d.get("target_id")}
                        )["name"]

                recent_votes = _decorate_recent_votes(
                    s.get_votes_by_voter(agent_id, limit=10),
                    agents_map, roles_by_id,
                )

                txs = s.list_transactions(limit=30, agent_id=agent_id)
                movements = _status_movements(s, agent_id, roles_by_id, cfg)
                return {
                    "id": a["agent_id"],
                    "name": a.get("name") or a["agent_id"],
                    "gender": a.get("gender"),
                    "role": role_id,
                    "role_label": role_spec.name if role_spec else role_id,
                    "role_prestige": role_spec.prestige if role_spec else None,
                    "class": a.get("class"),
                    "office": a.get("office"),
                    "personality": personality,
                    "values": values,
                    "backstory": a.get("backstory") or "",
                    "opinions": opinions,
                    "social_need": a.get("social_need"),
                    "prestige": a.get("prestige"),
                    "popularity": a.get("popularity"),
                    "state": a.get("state"),
                    "bank_account": a.get("bank_account"),
                    "recent_transactions": txs,
                    "status_movements": movements,
                    "memory": {
                        "summaries": summaries,
                        "impressions": impressions,
                    },
                    "recent_decisions": decisions,
                    "recent_votes": recent_votes,
                }
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/graphs/{name}.png")
    async def run_graph(run_id: int, name: str) -> Response:
        if name not in GRAPH_NAMES:
            raise HTTPException(404, f"unknown graph {name}")
        info = registry.get(run_id)
        if info is None:
            raise HTTPException(404, f"run {run_id} not found")
        cfg = _config_for_run(registry, run_id)
        db_path = Path(info["db_path"])
        run_subdir = runs_dir / db_path.stem
        graph_out = run_subdir / "graphs"
        graph_out.mkdir(parents=True, exist_ok=True)

        def _render() -> Path:
            with open_run_store(run_id) as s:
                renderer = GraphRenderer(cfg, s, graph_out)
                return renderer.render(name)

        try:
            path = await asyncio.to_thread(_render)
        except GraphUnavailable:
            raise HTTPException(404, f"graph {name} has no data")
        return FileResponse(path, media_type="image/png")

    @app.post("/runs/{run_id}/graphs/refresh")
    async def run_graphs_refresh(run_id: int) -> JSONResponse:
        info = registry.get(run_id)
        if info is None:
            raise HTTPException(404, f"run {run_id} not found")
        cfg = _config_for_run(registry, run_id)
        db_path = Path(info["db_path"])
        run_subdir = runs_dir / db_path.stem
        graph_out = run_subdir / "graphs"
        graph_out.mkdir(parents=True, exist_ok=True)

        def _render_all() -> dict:
            with open_run_store(run_id) as s:
                renderer = GraphRenderer(cfg, s, graph_out)
                return {k: str(v) for k, v in renderer.render_all().items()}

        return JSONResponse(await asyncio.to_thread(_render_all))

    def _decorate_messages(msgs: list[dict], agents: dict[str, dict]) -> None:
        """Attach sender/recipient display names in place."""
        for m in msgs:
            m["sender_name"] = agents.get(
                m["sender_id"], {"name": m["sender_id"]}
            )["name"]
            m["recipient_names"] = [
                agents.get(r, {"name": r})["name"] for r in m.get("recipients", [])
            ]

    # Event types that are conversational (covered by the Messages / Votes
    # tabs) and therefore excluded from the Actions tab.
    _NON_ACTION_EVENT_TYPES = [
        "broadcast_agent", "broadcast_human",
        "election_debate_speech", "vote_debate_speech",
        "vote_proposed",
    ]

    # Map each action event_type to a high-level category shown as its "type".
    _ACTION_CATEGORY = {
        "theft": "Crime", "theft_caught": "Crime",
        "sabotage": "Crime", "sabotage_caught": "Crime",
        "power_sabotaged": "Crime",
        "loan_granted": "Economy", "loan_denied": "Economy",
        "loan_repaid": "Economy", "gift": "Economy", "policy": "Economy",
        "destitution_entered": "Economy", "destitution_exited": "Economy",
        "election_held": "Politics", "office_change": "Politics",
        "decree": "Politics", "ruling": "Politics",
        "praise": "Influence", "smear": "Influence",
        "mobility_role_change": "Mobility", "mobility_class_change": "Mobility",
        "pause": "System", "simulation_failed": "System",
    }

    def _fmt_action_value(v: object) -> str:
        if isinstance(v, bool):
            return "yes" if v else "no"
        if isinstance(v, float):
            return f"{v:.2f}".rstrip("0").rstrip(".")
        return str(v)

    def _decorate_action(event: dict, agents: dict[str, dict]) -> dict:
        """Build a display-ready action row from a public_event."""
        kind = event.get("event_type") or "unknown"
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        def name_of(x: object) -> str:
            if isinstance(x, str) and x in agents:
                return agents[x]["name"]
            return _fmt_action_value(x)

        # Collect every agent referenced anywhere in the payload.
        involved_ids: list[str] = []

        def _scan(v: object) -> None:
            if isinstance(v, str):
                if v in agents and v not in involved_ids:
                    involved_ids.append(v)
            elif isinstance(v, list):
                for item in v:
                    _scan(item)
            elif isinstance(v, dict):
                for item in v.values():
                    _scan(item)

        _scan(payload)

        # Human-readable details: one "key: value" segment per payload field,
        # with agent ids resolved to names.
        parts: list[str] = []
        for k, v in payload.items():
            if isinstance(v, list):
                inner = ", ".join(
                    name_of(x) if not isinstance(x, dict) else "{…}" for x in v
                )
                parts.append(f"{k}: [{inner}]")
            elif isinstance(v, dict):
                parts.append(f"{k}: {{{len(v)} fields}}")
            else:
                parts.append(f"{k}: {name_of(v)}")
        details = " · ".join(parts)

        return {
            "id": event.get("id"),
            "ts": event.get("ts"),
            "kind": kind,
            "type": _ACTION_CATEGORY.get(kind, "Other"),
            "details": details,
            "agents_involved": [agents[a]["name"] for a in involved_ids],
            "payload": payload,
        }

    @app.get("/runs/{run_id}/messages")
    async def run_messages(
        run_id: int,
        limit: int = 500,
        sender: str | None = None,
        recipient: str | None = None,
        text: str | None = None,
    ) -> JSONResponse:
        """The async message feed (newest first) with optional filters."""
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                msgs = s.list_messages(
                    limit=limit, sender=sender, recipient=recipient, text=text
                )
                agents = _agents_map(s)
                _decorate_messages(msgs, agents)
                return {"messages": msgs, "agents": agents}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/messages/thread")
    async def run_message_thread(
        run_id: int, a: str, b: str, limit: int = 500
    ) -> JSONResponse:
        """Pairwise thread between two agents (oldest first)."""
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                msgs = s.messages_between(a, b, limit=limit)
                agents = _agents_map(s)
                _decorate_messages(msgs, agents)
                return {
                    "messages": msgs,
                    "agents": agents,
                    "a": a,
                    "b": b,
                    "a_name": agents.get(a, {"name": a})["name"],
                    "b_name": agents.get(b, {"name": b})["name"],
                }
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/actions")
    async def run_actions(run_id: int, limit: int = 1000) -> JSONResponse:
        """Agent actions during a run (public_events), excluding messages.

        Each action is decorated with a human-facing category (`type`), the
        raw `kind` (event_type), a short `details` summary, and the list of
        other agents involved (resolved to names).
        """
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                events = s.list_events_excluding(_NON_ACTION_EVENT_TYPES, limit=limit)
                agents = _agents_map(s)
                actions = [_decorate_action(e, agents) for e in events]
                return {"actions": actions, "agents": agents}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/events")
    async def run_events(run_id: int, limit: int = 100) -> JSONResponse:
        def _impl() -> list[dict]:
            with open_run_store(run_id) as s:
                return s.recent_events(limit)
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/elections")
    async def run_elections(run_id: int) -> JSONResponse:
        """Elections grouped by tick (newest first). Each entry summarises its
        office ballots (winner, incumbent, whether the office flipped, vote
        counts), any reassignment ballots, and the size of the shared debate."""
        def _impl() -> dict:
            cfg = _config_for_run(registry, run_id)
            roles_by_id = {r.id: r for r in cfg.roles}
            office_order = {oid: i for i, oid in enumerate(cfg.office_ids())}
            with open_run_store(run_id) as s:
                proposals = s.list_proposals()
                agents = _agents_map(s)
                debate_counts = Counter(
                    sp["tick"] for sp in s.election_debate_speeches()
                    if sp.get("tick") is not None
                )
            elections = _group_elections(
                proposals, agents, roles_by_id, office_order, debate_counts
            )
            return {"elections": elections, "agents": agents}
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/elections/{tick}")
    async def run_election_detail(run_id: int, tick: int) -> JSONResponse:
        """One election: the shared pre-ballot debate, each office ballot with
        its per-candidate tally and individual votes, and any reassignment
        ballots (tallied over ordinary roles)."""
        def _impl() -> dict:
            cfg = _config_for_run(registry, run_id)
            roles_by_id = {r.id: r for r in cfg.roles}
            office_order = {oid: i for i, oid in enumerate(cfg.office_ids())}
            with open_run_store(run_id) as s:
                ballots = [
                    p for p in s.list_proposals() if _proposal_tick(p) == tick
                ]
                if not ballots:
                    raise HTTPException(404, f"no election at tick {tick}")
                agents = _agents_map(s)
                debate = s.election_debate_speeches(tick)

                def agent_name(cid: str) -> str:
                    return agents.get(cid, {"name": cid})["name"]

                def role_label(rid: str) -> str:
                    role = roles_by_id.get(rid)
                    return role.name if role else rid

                offices: list[dict] = []
                reassignments: list[dict] = []
                for p in ballots:
                    if p.get("change_type") == "office":
                        summary = _office_summary(p, agents, roles_by_id)
                        summary.update(
                            _ballot_view(s.get_votes(p["id"]), agents, agent_name)
                        )
                        offices.append(summary)
                    elif p.get("change_type") == "reassign":
                        summary = _reassign_summary(p, agents, roles_by_id)
                        summary.update(
                            _ballot_view(s.get_votes(p["id"]), agents, role_label)
                        )
                        reassignments.append(summary)
                offices.sort(key=lambda o: office_order.get(o["office"], 10 ** 9))
            return {
                "tick": tick,
                "debate": debate,
                "offices": offices,
                "reassignments": reassignments,
                "agents": agents,
            }
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.get("/runs/{run_id}/community")
    async def run_community(run_id: int, limit: int = 500) -> JSONResponse:
        """Community treasury: current balance + transactions touching it."""
        def _impl() -> dict:
            with open_run_store(run_id) as s:
                balance = s.get_community_balance()
                # Pull a window of all transactions; the UI shows everything,
                # but movements relevant to the treasury are tagged.
                all_txs = s.list_transactions(limit=limit)
                community_txs: list[dict] = []
                for t in all_txs:
                    if t.get("from_party") == "community" or t.get("to_party") == "community":
                        community_txs.append(t)
                return {
                    "balance": balance,
                    "transactions": community_txs,
                }
        return JSONResponse(await asyncio.to_thread(_impl))

    @app.delete("/runs/{run_id}")
    async def delete_run(run_id: int) -> JSONResponse:
        info = registry.get(run_id)
        if info is None:
            raise HTTPException(404, f"run {run_id} not found")
        orch = session.orchestrator
        if orch is not None and run_id == orch.run_id and session.is_active:
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
