"""Read-only inspection of evaluation evidence, including incomplete episodes."""

import json
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException, Query

from ..benchmark.recipes import recipe_text
from ..persistence.event_store import EventStore


def policy_info(policy):
    if isinstance(policy, str):
        return dict(kind="scripted", name=policy)
    endpoint = urlsplit(policy["endpoint"])
    host = endpoint.hostname or ""
    if ":" in host:
        host = "[" + host + "]"
    if endpoint.port:
        host += ":" + str(endpoint.port)
    return dict(
        kind="model",
        name=policy["model"],
        snapshot=policy["snapshot"],
        context_tokens=policy.get("context_tokens"),
        endpoint=urlunsplit((endpoint.scheme, host, endpoint.path, "", "")),
    )


@contextmanager
def opened(path):
    if not path.is_file():
        raise HTTPException(404, "This episode has no recorded evidence yet")
    store = EventStore(path, read_only=True)
    try:
        store.db.execute("BEGIN")  # One consistent view while the runner commits new ticks.
        yield store
    finally:
        store.close()


def progress(path, manifest, live):
    if not path.is_file():
        return dict(
            status="pending",
            completed_tick=0,
            phase="Queued",
            saved_decisions=0,
            waiting=[],
            agents=[],
            provider_attempts=0,
        )
    with opened(path) as store:
        if store.manifest is None:
            return dict(
                status="pending",
                completed_tick=0,
                phase="Preparing episode",
                saved_decisions=0,
                waiting=[],
                agents=[],
                provider_attempts=0,
            )
        result = store.status()
        if result["status"] == "running" and not live:
            result["status"] = "interrupted"
        tick = result["completed_tick"]
        saved = {r[0] for r in store.db.execute("SELECT slot FROM decisions WHERE tick=?", (tick,))}
        state = store.load().dump()
        agents = [s for s, a in state["agents"].items() if a["alive"]]
        waiting = [s for s in agents if s not in saved] if tick < manifest["ticks"] else []
        phase = "Awaiting decisions" if waiting else "Committing world tick"
        if result["status"] != "running":
            phase = result["status"].replace("_", " ").capitalize()
        model_slots = [s for s, p in manifest["policies"].items() if isinstance(p, dict)]
        provider_attempts = store.db.execute(
            "SELECT COUNT(*) FROM model_calls WHERE json_extract(body,'$.slot') IN "
            "(SELECT value FROM json_each(?))",
            (json.dumps(model_slots),),
        ).fetchone()[0]
        return dict(
            result,
            phase=phase,
            saved_decisions=len(saved),
            waiting=waiting,
            agents=agents,
            provider_attempts=provider_attempts,
        )


def add_explorer_routes(router, campaign, folder, runner):
    def study_for(record, study_id):
        study = next((s for s in record["studies"] if s["id"] == study_id), None)
        if study is None:
            raise HTTPException(404, "Unknown study in this evaluation")
        return study

    def episode_for(ident, study_id, episode_id):
        record = campaign(ident)
        study = study_for(record, study_id)
        manifest = next(
            (m for m in study["execution"]["episodes"] if m["run_id"] == episode_id), None
        )
        if manifest is None:
            raise HTTPException(404, "Unknown episode in this study")
        path = folder("campaigns", ident) / "studies" / study["id"] / "episodes" / episode_id
        return record, study, manifest, path / "episode.db"

    def is_live(record, study):
        return record["id"] in runner.active and study["status"] == "running"

    @router.get("/evaluations/{ident}/explorer")
    def overview(ident: str):
        record = campaign(ident)
        live = ident in runner.active
        status = record["status"]
        if status == "running" and not live:
            status = "interrupted"
        studies = []
        for study in record["studies"]:
            manifests = study["execution"]["episodes"]
            state = study["status"]
            if state == "running" and not live:
                state = "interrupted"
            current = next((m for m in manifests if m["run_id"] == study["current_episode"]), None)
            details = None
            if current:
                path = folder("campaigns", ident) / "studies" / study["id"] / "episodes"
                details = progress(
                    path / current["run_id"] / "episode.db", current, is_live(record, study)
                )
            current_ids = study.get(
                "current_episodes",
                [study["current_episode"]] if study.get("current_episode") else [],
            )
            active_ticks = 0
            for manifest in manifests:
                if manifest["run_id"] in current_ids:
                    path = folder("campaigns", ident) / "studies" / study["id"] / "episodes"
                    active_ticks += progress(
                        path / manifest["run_id"] / "episode.db", manifest, is_live(record, study)
                    )["completed_tick"]
            phase = "Queued" if state == "pending" else state.replace("_", " ").capitalize()
            if state == "running":
                phase = details["phase"] if details else "Preparing episode"
                if study["completed_episodes"] == len(manifests):
                    phase = "Scoring study"
            studies.append(
                dict(
                    id=study["id"],
                    label=study["label"],
                    status=state,
                    phase=phase,
                    intervention=study["intervention"] or "normal",
                    error=study.get("error"),
                    completed_episodes=study["completed_episodes"],
                    total_episodes=len(manifests),
                    current_episode=study["current_episode"],
                    current_episodes=current_ids,
                    current_ticks=active_ticks,
                    current=details,
                    candidate=policy_info(manifests[0]["policies"][manifests[0]["focal_slot"]]),
                    provider_agents=sum(
                        isinstance(p, dict) for p in manifests[0]["policies"].values()
                    ),
                    score=study["report"]["usi"] if study.get("report") else None,
                )
            )
        phase = status.replace("_", " ").capitalize()
        if status == "running":
            phase = next(
                (s["phase"] for s in studies if s["status"] == "running"),
                "Analyzing evaluation"
                if all(s["status"] in {"completed", "failed"} for s in studies)
                else "Preparing study",
            )
        return dict(
            id=ident,
            name=record["name"],
            recipe_name=record["plan"]["name"],
            parallelism=record.get("parallelism", 1),
            split=record.get("split", "development"),
            status=status,
            phase=phase,
            error=recipe_text(record["error"]) if record.get("error") else None,
            studies=studies,
        )

    @router.get("/evaluations/{ident}/studies/{study_id}/episodes")
    def episodes(
        ident: str,
        study_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(48, ge=1, le=100),
    ):
        record = campaign(ident)
        study = study_for(record, study_id)
        manifests = study["execution"]["episodes"]
        rows = []
        for index, manifest in enumerate(manifests[offset : offset + limit], offset):
            path = folder("campaigns", ident) / "studies" / study["id"] / "episodes"
            detail = progress(
                path / manifest["run_id"] / "episode.db", manifest, is_live(record, study)
            )
            rows.append(
                dict(
                    index=index + 1,
                    id=manifest["run_id"],
                    **{k: manifest[k] for k in ["domain", "level", "seed", "role", "replicate"]},
                    ticks=manifest["ticks"],
                    **detail,
                )
            )
        return dict(
            items=rows,
            total=len(manifests),
            offset=offset,
            current_index=next(
                (i for i, m in enumerate(manifests) if m["run_id"] == study["current_episode"]),
                None,
            ),
        )

    prefix = "/evaluations/{ident}/studies/{study_id}/episodes/{episode_id}"

    @router.get(prefix)
    def episode_detail(ident: str, study_id: str, episode_id: str):
        record, study, manifest, path = episode_for(ident, study_id, episode_id)
        detail = progress(path, manifest, is_live(record, study))
        policies = {s: policy_info(p) for s, p in manifest["policies"].items()}
        base = dict(
            detail,
            id=episode_id,
            policies=policies,
            focal_slot=manifest["focal_slot"],
            ticks=manifest["ticks"],
            intervention=study["intervention"] or "normal",
            error=study.get("error"),
            series=[],
            phases=[],
            totals={},
            last_decision_tick=None,
        )
        if detail["status"] == "pending":
            return base
        with opened(path) as store:
            base["phases"] = [
                dict(phase=p, events=n)
                for p, n in store.db.execute(
                    "SELECT json_extract(body,'$.phase'),COUNT(*) FROM events WHERE tick=? "
                    "AND json_extract(body,'$.type') NOT IN ('world_initialized','state_committed') "
                    "GROUP BY json_extract(body,'$.phase') ORDER BY MIN(seq)",
                    (detail["completed_tick"],),
                )
            ]
            event_rows = store.db.execute(
                "SELECT tick, COUNT(*), SUM(json_extract(body,'$.type')='message_sent'), "
                "SUM(json_extract(body,'$.type') IN ('operation_rejected','decision_rejected')) "
                "FROM events WHERE json_extract(body,'$.type') NOT IN "
                "('world_initialized','state_committed') GROUP BY tick ORDER BY tick"
            ).fetchall()
            base["series"] = [
                dict(tick=t, events=n, messages=m, rejected=r) for t, n, m, r in event_rows
            ]
            model_slots = [s for s, p in policies.items() if p["kind"] == "model"]
            calls = store.db.execute(
                "SELECT COUNT(*), SUM(json_extract(body,'$.generated_tokens')), "
                "SUM(json_extract(body,'$.input_tokens')), AVG(json_extract(body,'$.latency')), "
                "SUM(json_extract(body,'$.error') IS NOT NULL) FROM model_calls "
                "WHERE json_extract(body,'$.slot') IN (SELECT value FROM json_each(?))",
                (json.dumps(model_slots),),
            ).fetchone()
            base["totals"] = dict(
                provider_attempts=calls[0],
                output_tokens=calls[1],
                input_tokens=calls[2],
                mean_latency=calls[3],
                errors=calls[4] or 0,
            )
            base["last_decision_tick"] = store.db.execute(
                "SELECT MAX(tick) FROM decisions"
            ).fetchone()[0]
        return base

    @router.get(prefix + "/state")
    def state(ident: str, study_id: str, episode_id: str, tick: int = Query(ge=0)):
        _, _, _, path = episode_for(ident, study_id, episode_id)
        with opened(path) as store:
            row = store.db.execute("SELECT state FROM snapshots WHERE tick=?", (tick,)).fetchone()
            if row is None:
                raise HTTPException(404, "No saved world state at this tick")
            return json.loads(row[0])

    @router.get(prefix + "/decision")
    def decision(ident: str, study_id: str, episode_id: str, agent: str, tick: int = Query(ge=0)):
        _, _, manifest, path = episode_for(ident, study_id, episode_id)
        if agent not in manifest["policies"]:
            raise HTTPException(404, "Unknown agent")
        with opened(path) as store:
            row = store.db.execute(
                "SELECT observation,raw,request_id FROM decisions WHERE tick=? AND slot=?",
                (tick, agent),
            ).fetchone()
            if row is None:
                return dict(recorded=False, observation=None, response=None, calls=[])
            calls = [
                json.loads(r[0])
                for r in store.db.execute(
                    "SELECT body FROM model_calls WHERE request_id=? ORDER BY attempt", (row[2],)
                )
            ]
            return dict(recorded=True, observation=json.loads(row[0]), response=row[1], calls=calls)

    @router.get(prefix + "/events")
    def events(
        ident: str,
        study_id: str,
        episode_id: str,
        after: int = Query(0, ge=0),
        kind: str = Query("all", pattern="^(all|messages|errors)$"),
        limit: int = Query(60, ge=1, le=200),
    ):
        _, _, _, path = episode_for(ident, study_id, episode_id)
        with opened(path) as store:
            clause = "json_extract(body,'$.type') NOT IN ('world_initialized','state_committed')"
            if kind == "messages":
                clause = "json_extract(body,'$.type')='message_sent'"
            elif kind == "errors":
                clause = "json_extract(body,'$.type') IN ('operation_rejected','decision_rejected')"
            rows = [
                json.loads(r[0])
                for r in store.db.execute(
                    f"SELECT body FROM events WHERE seq>? AND {clause} ORDER BY seq LIMIT ?",
                    (after, limit + 1),
                )
            ]
            items = rows[:limit]
            return dict(
                items=items, more=len(rows) > limit, cursor=items[-1]["seq"] if items else after
            )

    @router.get(prefix + "/calls")
    def calls(
        ident: str,
        study_id: str,
        episode_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(40, ge=1, le=100),
    ):
        _, _, manifest, path = episode_for(ident, study_id, episode_id)
        slots = [s for s, p in manifest["policies"].items() if isinstance(p, dict)]
        with opened(path) as store:
            rows = store.db.execute(
                "SELECT c.request_id,c.attempt,c.body,d.tick FROM model_calls c "
                "LEFT JOIN decisions d ON c.request_id=d.request_id "
                "WHERE json_extract(c.body,'$.slot') IN (SELECT value FROM json_each(?)) "
                "ORDER BY c.rowid LIMIT ? OFFSET ?",
                (json.dumps(slots), limit + 1, offset),
            ).fetchall()
            return dict(
                items=[
                    dict(request_id=r, attempt=a + 1, tick=t, **json.loads(b))
                    for r, a, b, t in rows[:limit]
                ],
                more=len(rows) > limit,
                offset=offset,
            )
