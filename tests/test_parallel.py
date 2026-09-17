import asyncio

import pytest

from unimatrix.benchmark.parallel import execute_episodes, request_gate, validate_parallelism


@pytest.mark.asyncio
async def test_out_of_order_completion_pause_and_resume():
    manifests = [{"run_id": ident} for ident in "abc"]
    record = {"completed_episodes": 0}
    saved = asyncio.Event()
    waiting = asyncio.Event()
    stopped = []

    async def worker(manifest):
        ident = manifest["run_id"]
        if ident == "b":
            return {"calls": 2}
        try:
            await waiting.wait()
        finally:
            stopped.append(ident)

    def save():
        if "b" in record["completed_episode_ids"]:
            saved.set()

    task = asyncio.create_task(
        execute_episodes(record, manifests, worker, save, 2, "usage", ["calls"])
    )
    await asyncio.wait_for(saved.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert record["completed_episode_ids"] == ["b"]
    assert "a" in stopped
    visited = []

    async def resume(manifest):
        visited.append(manifest["run_id"])
        return {"calls": 1}

    await execute_episodes(record, manifests, resume, lambda: None, 2, "usage", ["calls"])
    assert set(visited) == {"a", "c"}
    assert record["completed_episodes"] == 3
    assert record["usage"]["calls"] == 4
    assert record["current_episodes"] == [] and record["current_episode"] is None
    assert request_gate.get() is None


@pytest.mark.asyncio
async def test_worker_failure_drains_other_workers():
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def worker(manifest):
        if manifest["run_id"] == "a":
            await started.wait()
            raise RuntimeError("failed")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    with pytest.raises(RuntimeError, match="failed"):
        await execute_episodes(
            {"completed_episodes": 0},
            [{"run_id": x} for x in "ab"],
            worker,
            lambda: None,
            2,
            "usage",
            [],
        )
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_shared_limit_across_real_episodes_and_identical_world_results(tmp_path, monkeypatch):
    from unimatrix.actions.schemas import empty
    from unimatrix.benchmark.plans import PlanRepository, bind_candidate
    from unimatrix.benchmark.scheduler import run_episode
    from unimatrix.core.ids import canonical
    from unimatrix.persistence.event_store import EventStore
    from unimatrix.policies.llm_policy import LLMPolicy

    model = dict(
        model="fake",
        snapshot="v1",
        endpoint="http://unused",
        context_bytes_verified=24000,
        budget_track="opaque_compute",
    )
    spec = PlanRepository().get("compact-v1")
    spec["cases"] = spec["cases"][:2]
    spec["domains"] = {
        d: v for d, v in spec["domains"].items() if d in {c["domain"] for c in spec["cases"]}
    }
    spec["peers"][:2] = [model, model]
    manifests = bind_candidate(spec, model)["episodes"]
    active = peak = 0
    overlap = asyncio.Event()

    async def decide(self, observation, budget):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            overlap.set()
        try:
            await asyncio.wait_for(overlap.wait(), 2)
            await asyncio.sleep(0)
            return canonical(empty(observation["tick"], observation["agent_id"])), {}
        finally:
            active -= 1

    monkeypatch.setattr(LLMPolicy, "decide", decide)
    for limit in [2, 1]:
        folder = tmp_path / str(limit)

        async def worker(manifest):
            await run_episode(manifest, folder, until=2)
            return {}

        await execute_episodes(
            {"completed_episodes": 0}, manifests, worker, lambda: None, limit, "usage", []
        )
        assert peak == limit
        peak = 0
    for manifest in manifests:
        states = []
        for limit in [2, 1]:
            store = EventStore(
                tmp_path / str(limit) / manifest["run_id"] / "episode.db", read_only=True
            )
            try:
                states.append(store.load().dump())
            finally:
                store.close()
        assert states[0] == states[1]


@pytest.mark.parametrize("value", [0, -1, 65, True, 1.5, "4"])
def test_invalid_parallelism(value):
    with pytest.raises(ValueError):
        validate_parallelism(value)


@pytest.mark.asyncio
async def test_parallelism_is_saved_by_both_launch_interfaces(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI

    from unimatrix.persistence.json_files import write_json
    from unimatrix.web.benchmark import build_router
    from unimatrix.web.research import build_research_router

    root, models, recipes = tmp_path / "runs", tmp_path / "models", tmp_path / "recipes"
    write_json(
        models / "fake.json",
        dict(
            model="fake",
            snapshot="v1",
            endpoint="http://unused",
            context_bytes_verified=24000,
            budget_track="opaque_compute",
        ),
    )
    router = build_router(root, models, recipes)
    monkeypatch.setattr(router.service, "launch", router.service.save)
    app = FastAPI()
    app.include_router(router)
    app.include_router(build_research_router(root, models, recipes, "standard-v1"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/benchmarks", json={"model_id": "fake", "parallelism": 4})
        assert response.status_code == 200, response.text
        assert router.service.read(response.json()["id"])["parallelism"] == 4
        for value in [0, 65, True, 2.5]:
            assert (
                await client.post(
                    "/api/benchmarks", json={"model_id": "fake", "parallelism": value}
                )
            ).status_code == 422
        response = await client.post(
            "/api/recipe-lab/drafts",
            json=dict(
                base_recipe="compact-v1",
                id="parallel-test",
                name="Parallel test",
                description="Test",
                domains=["D1"],
                seeds=[100, 101],
                roles=["advantaged"],
            ),
        )
        assert response.status_code == 200, response.text
        body = dict(
            draft_id=response.json()["id"],
            name="Parallel evaluation",
            model_ids=["fake"],
            baselines=[],
            interventions=[],
            parallelism=4,
        )
        response = await client.post("/api/recipe-lab/evaluation-preview", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["parallelism"] == 4
        body["parallelism"] = 2
        other = await client.post("/api/recipe-lab/evaluation-preview", json=body)
        assert other.json()["id"] != response.json()["id"]
