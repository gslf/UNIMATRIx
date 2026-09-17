"""Deletion removes the selected evidence, including all recipe revisions."""

import json

import httpx
import pytest

from unimatrix.persistence.json_files import write_json
from unimatrix.persistence.lease import episode_lease
from unimatrix.web.server import build_app


async def draft(client, ident="delete-me", seed=100):
    response = await client.post(
        "/api/recipe-lab/drafts",
        json=dict(
            base_recipe="compact-v1",
            id=ident,
            name=ident,
            description="Deletion test",
            domains=["D1"],
            seeds=[seed],
            holdout_seeds=[200],
            roles=["advantaged"],
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def evidence(client, root, record, ident, split="development"):
    preview = await client.post(
        "/api/recipe-lab/evaluation-preview",
        json=dict(
            draft_id=record["id"],
            name="Test evaluation",
            split=split,
            model_ids=[],
            baselines=["passive"],
            interventions=[],
        ),
    )
    assert preview.status_code == 200, preview.text
    prepared = json.loads(
        (root / "research/previews" / preview.json()["id"] / "preview.json").read_text()
    )
    prepared.update(id=ident, status="paused")
    folder = root / "research/campaigns" / ident
    write_json(folder / "campaign.json", prepared)
    write_json(folder / "studies/0/episodes/evidence.json", {"keep": True})
    return folder, preview.json()["id"]


@pytest.mark.asyncio
async def test_delete_evaluation_leaves_recipe_models_and_other_evidence(tmp_path):
    root = tmp_path / "runs"
    app = build_app(root, tmp_path / "models", tmp_path / "recipes")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        record = await draft(client)
        folder, _ = await evidence(client, root, record, "a" * 24)
        other, _ = await evidence(client, root, record, "b" * 24)
        response = await client.delete("/api/recipe-lab/evaluations/" + "a" * 24)
        assert response.status_code == 200, response.text
        assert not folder.exists() and other.exists()
        assert (await client.get("/api/recipe-lab/drafts/" + record["id"])).status_code == 200
        assert (await client.get("/api/recipe-lab/evaluations/" + "a" * 24)).status_code == 404
        assert (await client.delete("/api/recipe-lab/evaluations/" + "a" * 24)).status_code == 404
        assert (await client.get("/api/recipe-lab/campaigns")).json() == (
            await client.get("/api/recipe-lab/evaluations")
        ).json()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["draft", "published"])
async def test_recipe_deletion_cascades_all_revisions_splits_and_previews(tmp_path, source):
    root = tmp_path / "runs"
    models = tmp_path / "models"
    models.mkdir()
    sentinel = models / "untouched.txt"
    sentinel.write_text("model credentials remain")
    recipes = tmp_path / "recipes"
    app = build_app(root, models, recipes)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await draft(client)
        second = await draft(client, seed=101)
        unrelated = await draft(client, "keep-me")
        folders = []
        previews = []
        for rec, ident, split in [(first, "a", "development"), (second, "b", "holdout")]:
            folder, preview = await evidence(client, root, rec, ident * 24, split)
            folders.append(folder)
            previews.append(preview)
        keep, _ = await evidence(client, root, unrelated, "c" * 24)
        for split in ["development", "holdout"]:
            response = await client.post(
                f"/api/recipe-lab/drafts/{first['id']}/publish", json={"split": split}
            )
            assert response.status_code == 200
        # Authored filenames are not necessarily equal to recipe IDs.
        (recipes / "delete-me.json").rename(recipes / "custom-filename.json")
        benchmark = root / ("d" * 24)
        write_json(benchmark / "benchmark.json", {"plan_id": "delete-me"})
        path = (
            f"/api/recipe-lab/drafts/{first['id']}"
            if source == "draft"
            else "/api/recipe-lab/recipes/delete-me"
        )
        impact = (await client.get(path + "/deletion")).json()
        assert impact["evaluations"] == 2 and impact["drafts"] == 2
        assert impact["benchmarks"] == 1
        response = await client.delete(path)
        assert response.status_code == 200, response.text
        assert all(not folder.exists() for folder in folders)
        assert not benchmark.exists() and keep.exists() and sentinel.exists()
        assert list(recipes.glob("*.json")) == []
        for ident in previews:
            assert not (root / "research/previews" / ident).exists()
            assert (
                await client.post("/api/recipe-lab/evaluations", json={"preview_id": ident})
            ).status_code == 404
        remaining = (await client.get("/api/recipe-lab/drafts")).json()
        assert [r["id"] for r in remaining] == [unrelated["id"]]


@pytest.mark.asyncio
async def test_deletion_refuses_active_runner_and_linked_directories(tmp_path):
    root = tmp_path / "runs"
    app = build_app(root, tmp_path / "models", tmp_path / "recipes")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        record = await draft(client)
        folder, _ = await evidence(client, root, record, "a" * 24)
        paths = [
            "/api/recipe-lab/evaluations/" + "a" * 24,
            "/api/recipe-lab/drafts/" + record["id"],
        ]
        with episode_lease(root / "benchmark.lock"):
            for path in paths:
                response = await client.delete(path)
                assert response.status_code == 409
                assert "Pause" in response.json()["detail"]
        assert folder.exists()
        outside = tmp_path / "outside"
        folder.rename(outside)
        folder.symlink_to(outside, target_is_directory=True)
        assert (await client.delete(paths[0])).status_code == 409
        assert (outside / "campaign.json").exists()
        assert (await client.delete("/api/recipe-lab/evaluations/not-an-id")).status_code == 404


@pytest.mark.asyncio
async def test_default_recipe_deletion_is_blocked_without_partial_cascade(tmp_path):
    root, recipes = tmp_path / "runs", tmp_path / "recipes"
    app = build_app(root, tmp_path / "models", recipes)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        record = await draft(client)
        await client.post(f"/api/recipe-lab/drafts/{record['id']}/publish", json={})
        folder, _ = await evidence(client, root, record, "a" * 24)
    app = build_app(root, tmp_path / "models", recipes, default_recipe="delete-me")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.delete(f"/api/recipe-lab/drafts/{record['id']}")
        assert response.status_code == 409 and "default" in response.json()["detail"]
        assert folder.exists() and (recipes / "delete-me.json").exists()
        catalog = (await client.get("/api/recipes")).json()
        assert not next(r for r in catalog if r["recipe_id"] == "delete-me")["deletable"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "paused", "failed", "running"])
async def test_delete_single_benchmark_preserves_other_data(tmp_path, monkeypatch, status):
    from fastapi import FastAPI

    from unimatrix.web.benchmark import build_router

    root = tmp_path / "runs"
    router = build_router(root, tmp_path / "models", tmp_path / "recipes")
    app = FastAPI()
    app.include_router(router)
    service = router.service
    # Persist a realistic run without starting a worker or making provider calls.
    monkeypatch.setattr(service, "launch", service.save)
    run = await service.start("passive", "test")
    other = await service.start("random", "other")
    stored = service.read(run["id"])
    stored["status"] = status
    service.save(stored)
    folder = service.folder(run["id"])
    write_json(folder / "episodes/example/evidence.json", {"decision": "recorded"})
    sentinel = root / "research/campaigns/keep/campaign.json"
    write_json(sentinel, {"keep": True})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = "/api/benchmarks/" + run["id"]
        impact = (await client.get(path + "/deletion")).json()
        assert impact["id"] == run["id"] and impact["benchmarks"] == 1
        assert impact["episodes"] == run["total_episodes"]
        assert impact["evaluations"] == 0 and "paths" not in impact
        response = await client.delete(path)
        assert response.status_code == 200, response.text
        assert not folder.exists()
        assert service.folder(other["id"]).exists() and sentinel.exists()
        assert [r["id"] for r in (await client.get("/api/benchmarks")).json()] == [other["id"]]
        assert any(
            r["recipe_id"] == "standard-v1" for r in (await client.get("/api/recipes")).json()
        )
        assert (await client.get(path)).status_code == 404
        assert (await client.delete(path)).status_code == 404
        assert (await client.get(path + "/deletion")).status_code == 404


@pytest.mark.asyncio
async def test_benchmark_delete_checks_lock_and_rejects_linked_paths(tmp_path):
    root = tmp_path / "runs"
    app = build_app(root, tmp_path / "models", tmp_path / "recipes")
    ident = "a" * 24
    folder = root / ident
    write_json(folder / "benchmark.json", {"model": "test", "total_episodes": 1})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = "/api/benchmarks/" + ident
        with episode_lease(root / "benchmark.lock"):
            response = await client.delete(path)
            assert response.status_code == 409
            assert "Pause" in response.json()["detail"]
        assert folder.exists()
        outside = tmp_path / "outside-run"
        folder.rename(outside)
        folder.symlink_to(outside, target_is_directory=True)
        assert (await client.delete(path)).status_code == 409
        assert (outside / "benchmark.json").exists()
        assert (await client.delete("/api/benchmarks/not-an-id")).status_code == 404
