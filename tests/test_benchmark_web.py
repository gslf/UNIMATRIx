import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI

from unimatrix.actions.schemas import empty
from unimatrix.benchmark.plans import PlanRepository
from unimatrix.benchmark.service import BenchmarkService
from unimatrix.core.ids import canonical
from unimatrix.persistence.json_files import write_json
from unimatrix.policies.llm_policy import LLMPolicy
from unimatrix.web.benchmark import build_router

MODEL = dict(
    model="competitor-a",
    snapshot="revision-1",
    endpoint="http://unused",
    context_bytes_verified=24000,
    budget_track="accounted_compute",
)


def setup(tmp_path):
    spec = PlanRepository().get("compact-v1")
    spec.update(
        id="test-v1",
        name="Test v1",
        cases=spec["cases"][:1],
        domains={"D1": spec["domains"]["D1"]},
        bootstrap=dict(seed=1, resamples=10),
    )
    write_json(tmp_path / "plans" / "test.json", spec)
    write_json(tmp_path / "models" / "a.json", MODEL)
    router = build_router(tmp_path / "runs", tmp_path / "models", tmp_path / "plans", "test-v1")
    app = FastAPI()
    app.include_router(router)
    return app, router, spec


@pytest.mark.asyncio
async def test_complete_benchmark_scores_and_exposes_real_evidence(tmp_path, monkeypatch):
    async def decide(self, observation, budget):
        assert budget["generation_tokens"] is None
        result = empty(observation["tick"], observation["agent_id"])
        result["messages"] = [dict(channel="public", to=[], content="Recorded test message")]
        return canonical(result), dict(generated_tokens=20, input_tokens=50, purpose="decision")

    monkeypatch.setattr(LLMPolicy, "decide", decide)
    app, router, spec = setup(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Clients select saved recipes, but cannot supply arbitrary benchmark conditions.
        bad = await client.post("/api/benchmarks", json={"model_id": "a", "plan": "custom"})
        assert bad.status_code == 422
        response = await client.post("/api/benchmarks", json={"model_id": "a"})
        assert response.status_code == 200
        run = response.json()
        assert run["plan_id"] == "test-v1"
        assert (
            await client.get(f"/api/leaderboard?cohort={run['cohort']}&track=accounted_compute")
        ).json() == []
        await router.service.active[run["id"]][0]
        result = (await client.get("/api/benchmarks/" + run["id"])).json()
        assert result["status"] == "completed", result.get("error")
        assert result["report"]["completed_episodes"] == 1
        assert 0 <= result["report"]["usi"] <= 100
        assert (
            len(
                (
                    await client.get(
                        f"/api/leaderboard?cohort={run['cohort']}&track=accounted_compute"
                    )
                ).json()
            )
            == 1
        )
        assert (
            await client.get(f"/api/leaderboard?cohort={run['cohort']}&track=opaque_compute")
        ).json() == []
        episode = result["episodes"][0]
        prefix = f"/api/benchmarks/{run['id']}/episodes/{episode['run_id']}"
        detail = (await client.get(prefix)).json()
        assert len(detail["series"]) == 241
        assert detail["metrics"]
        assert detail["verification"]["completed_tick"] == 240
        decision = (
            await client.get(
                prefix + "/decision", params={"tick": 0, "agent": episode["focal_slot"]}
            )
        ).json()
        assert "Recorded test message" in decision["response"]
        assert decision["calls"][0]["generated_tokens"] == 20
        assert decision["observation"]["agent_id"] == episode["focal_slot"]
        messages = (await client.get(prefix + "/events?kind=messages&limit=2")).json()
        assert len(messages["items"]) == 2 and messages["more"]
        next_page = (
            await client.get(prefix + f"/events?kind=messages&after={messages['cursor']}&limit=2")
        ).json()
        assert next_page["items"][0]["seq"] > messages["cursor"]
        assert (await client.get(prefix + "/state?tick=0")).json()["tick"] == 0
        assert (await client.get(f"/api/benchmarks/{run['id']}/export")).status_code == 200
        # Archived plan revisions stay inspectable after an authored file changes.
        changed = deepcopy(spec)
        changed["cases"][0]["seed"] = 17
        write_json(tmp_path / "plans" / "test.json", changed)
        versions = (await client.get("/api/plans")).json()
        old = next(p for p in versions if p["id"] == run["cohort"])
        assert old["archived"]
        current = next(p for p in versions if p["default"])
        assert current["id"] != old["id"]
        assert (
            await client.get(f"/api/leaderboard?cohort={current['id']}&track=accounted_compute")
        ).json() == []
    await router.shutdown_tasks()


@pytest.mark.asyncio
async def test_pause_restart_resume_and_failure_never_rank(tmp_path, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def decide(self, observation, budget):
        started.set()
        await release.wait()
        return canonical(empty(observation["tick"], observation["agent_id"])), dict(
            generated_tokens=1
        )

    monkeypatch.setattr(LLMPolicy, "decide", decide)
    _, router, _ = setup(tmp_path)
    service = router.service
    run = await service.start(MODEL, "a")
    await started.wait()
    with pytest.raises(ValueError, match="already running"):
        await service.start(MODEL, "a")
    await service.pause(run["id"])
    assert service.read(run["id"])["status"] == "paused"
    assert service.leaderboard(run["cohort"], "accounted_compute") == []
    # A new server instance uses the saved plan, even if the author changes the live file.
    restored = BenchmarkService(tmp_path / "runs", service.plans)
    await restored.resume(run["id"])
    release.set()
    await restored.active[run["id"]][0]
    assert restored.read(run["id"])["status"] == "completed"

    # Failure remains visible with its error; it cannot be assigned a score.
    async def fail(*args):
        raise ValueError("test provider failure")

    monkeypatch.setattr(LLMPolicy, "decide", fail)
    failed = await restored.start(dict(MODEL, model="failed-model"), "bad")
    await restored.active[failed["id"]][0]
    assert restored.read(failed["id"])["status"] == "failed"
    assert len(restored.leaderboard(run["cohort"], "accounted_compute")) == 1
    await restored.shutdown()


@pytest.mark.asyncio
async def test_cancel_before_first_instruction_releases_lease(tmp_path):
    _, router, _ = setup(tmp_path)
    run = await router.service.start(MODEL, "a")
    await router.shutdown_tasks()
    assert router.service.read(run["id"])["status"] == "paused"
    other = await router.service.start(MODEL, "a")
    assert other["id"] != run["id"]
    await router.shutdown_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("recipe_id", ["compact-v1", "custom-v2"])
async def test_start_runs_selected_recipe_instead_of_server_default(tmp_path, monkeypatch, recipe_id):
    app, router, spec = setup(tmp_path)
    custom = dict(spec, id="custom-v2", name="Custom v2")
    write_json(tmp_path / "plans" / "custom.json", custom)

    async def wait_for_pause(run):
        await asyncio.Event().wait()

    monkeypatch.setattr(router.service, "execute", wait_for_pause)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            catalog = (await client.get("/api/recipes")).json()
            selected = next(r for r in catalog if r["recipe_id"] == recipe_id)
            response = await client.post("/api/benchmarks", json={
                "model_id": "a", "recipe_id": recipe_id, "cohort": selected["id"],
            })
            assert response.status_code == 200, response.text
            run = router.service.read(response.json()["id"])
            assert run["plan_id"] == recipe_id
            assert run["cohort"] == selected["id"]
            assert run["total_episodes"] == selected["cases"]
            assert run["execution"]["suite"]["cases"] == selected["spec"]["cases"]
            assert router.service.plans.default == "test-v1"
    finally:
        await router.shutdown_tasks()


@pytest.mark.asyncio
async def test_start_rejects_missing_or_changed_recipe_without_default_fallback(tmp_path):
    app, router, spec = setup(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        selected = next(r for r in (await client.get("/api/recipes")).json() if r["default"])
        changed = deepcopy(spec)
        changed["cases"][0]["seed"] += 1
        write_json(tmp_path / "plans" / "test.json", changed)
        response = await client.post("/api/benchmarks", json={
            "model_id": "a", "recipe_id": "test-v1", "cohort": selected["id"],
        })
        assert response.status_code == 409
        for recipe_id in ["missing-recipe", ""]:
            response = await client.post("/api/benchmarks", json={
                "model_id": "a", "recipe_id": recipe_id,
            })
            assert response.status_code == 422
        assert not router.service.runs() and not router.service.active
