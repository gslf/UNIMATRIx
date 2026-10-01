"""Cumulative wall-time controls and complete, bounded recipe panels."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from itertools import product

import pytest

from unimatrix.benchmark.duration import estimate, recover, remaining
from unimatrix.benchmark.recipes import RecipeRepository, validate_recipe
from unimatrix.benchmark.service import BenchmarkService
from unimatrix.scenarios.layers import layers_key


def spec_with_budget(seconds):
    spec = deepcopy(RecipeRepository().get("standard-v1"))
    spec["max_wall_seconds"] = seconds
    return spec


def test_bundled_panels_cover_domains_and_standard_crosses_conditions():
    panels = RecipeRepository().all()
    previous_seeds = set()
    previous_size = previous_budget = 0
    for name in ("standard-v1", "validation-v1"):
        spec = panels[name]
        assert spec["ticks"] == 72
        cases = spec["cases"]
        assert {c["domain"] for c in cases} == {f"D{i}" for i in range(1, 9)}
        assert len(cases) > previous_size
        assert previous_budget < spec["max_wall_seconds"] <= 52 * 3600
        assert {c["role"] for c in cases} == {"advantaged", "disadvantaged"}
        assert {layers_key(c["layers"]) for c in cases} == {"standard", "stress"}
        seeds = {c["seed"] for c in cases}
        assert len(seeds) == len(cases)
        assert not seeds & previous_seeds
        previous_seeds |= seeds
        previous_size, previous_budget = len(cases), spec["max_wall_seconds"]
    actual = {(c["domain"], layers_key(c["layers"])) for c in panels["standard-v1"]["cases"]}
    assert actual == set(product([f"D{i}" for i in range(1, 9)], ["standard", "stress"]))
    validation = panels["validation-v1"]
    cells = {(c["domain"], layers_key(c["layers"]), c["role"]) for c in validation["cases"]}
    assert cells == set(product([f"D{i}" for i in range(1, 9)],
                                ["standard", "stress"], ["advantaged", "disadvantaged"]))
    assert all(sum((c["domain"], layers_key(c["layers"]), c["role"]) == cell
                   for c in validation["cases"]) == 4 for cell in cells)


@pytest.mark.parametrize("value", [0, -1, True, "100", float("nan"), float("inf"), 52 * 3600 + 1])
def test_invalid_wall_budget_rejected(value):
    with pytest.raises(ValueError, match="wall-time"):
        validate_recipe(spec_with_budget(value))


def test_estimate_uses_workload_and_warns_before_overrun():
    spec = RecipeRepository().get("standard-v1")
    assert estimate(spec, 42)["fits_budget"]
    assert not estimate(spec, 60)["fits_budget"]
    projection = estimate(spec, 42)
    assert projection["projected_provider_seconds"] == pytest.approx(20.16 * 3600)
    assert projection["available_nonprovider_seconds"] == pytest.approx(5.84 * 3600)
    model = dict(model="m", snapshot="s", endpoint="http://localhost", context_tokens=50000, budget_track="opaque_compute")
    spec["peers"][0] = model
    assert estimate(spec, 42)["provider_decisions"] == 2 * projection["provider_decisions"]


def test_crash_elapsed_time_is_charged_once_and_clean_pause_costs_nothing():
    record = dict(frozen_recipe=spec_with_budget(100), wall_seconds=12,
                  budget_checkpoint_at=(datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat())
    recover(record)
    assert 32 <= record["wall_seconds"] < 33
    charged = record["wall_seconds"]
    recover(record)
    assert record["wall_seconds"] == charged
    assert 66 <= remaining(record) <= 67


@pytest.mark.asyncio
async def test_exhaustion_cancels_work_retains_evidence_and_refuses_resume(tmp_path, monkeypatch):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    service = BenchmarkService(tmp_path, RecipeRepository())

    async def work(run):
        (service.folder(run["id"]) / "retained-evidence.txt").write_text("successful response")
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(service, "_execute_work", work)
    run = await service.start("passive", "test", spec_with_budget(0.12))
    await entered.wait()
    await service.active[run["id"]][0]
    result = service.read(run["id"])
    assert cancelled.is_set()
    assert result["status"] == "budget_exhausted" and result["report"] is None
    assert result["wall_seconds"] >= 0.1
    assert "budget_checkpoint_at" not in result
    assert (service.folder(run["id"]) / "retained-evidence.txt").read_text() == "successful response"
    with pytest.raises(ValueError, match="budget_exhausted"):
        await service.resume(run["id"])
    assert not service.active and not service._clocks


@pytest.mark.asyncio
async def test_pause_resume_cannot_reset_time_and_cancellation_before_start_is_accounted(tmp_path, monkeypatch):
    service = BenchmarkService(tmp_path, RecipeRepository())
    entered = asyncio.Event()

    async def work(run):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_execute_work", work)
    run = await service.start("passive", "test", spec_with_budget(2))
    await service.pause(run["id"])
    assert service.read(run["id"])["wall_seconds"] > 0
    assert not service.active and not service._clocks
    await service.resume(run["id"])
    await entered.wait()
    await asyncio.sleep(0.03)
    await service.pause(run["id"])
    saved = service.read(run["id"])
    spent = saved["wall_seconds"]
    assert spent >= 0.03
    await service.resume(run["id"])
    await service.pause(run["id"])
    assert service.read(run["id"])["wall_seconds"] >= spent
    assert service.read(run["id"])["status"] == "paused"


@pytest.mark.asyncio
async def test_recovery_cannot_bypass_budget_by_restarting_service(tmp_path, monkeypatch):
    service = BenchmarkService(tmp_path, RecipeRepository())
    run = await service.start("passive", "test", spec_with_budget(1))
    await service.pause(run["id"])
    saved = service.read(run["id"])
    saved["status"] = "running"
    saved["budget_checkpoint_at"] = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    service.save(saved)
    restarted = BenchmarkService(tmp_path, RecipeRepository())
    with pytest.raises(ValueError, match="budget_exhausted"):
        await restarted.resume(run["id"])
    assert restarted.read(run["id"])["status"] == "budget_exhausted"
