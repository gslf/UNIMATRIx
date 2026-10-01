from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI

from tests.test_research import MODEL, small_plan
from unimatrix.core.ids import digest
from unimatrix.persistence.json_files import write_json
from unimatrix.research.campaigns import prepare
from unimatrix.research.comparison import compare_smoke, compatible, paired_summary
from unimatrix.scenarios.layers import resolve_layers
from unimatrix.web.research import build_research_router


def completed(record, folder, scores):
    record["status"] = "completed"
    for study in record["studies"]:
        spec = study["execution"]["suite"]
        study["status"] = "completed"
        study["completed_episodes"] = len(spec["cases"])
        study["report"] = dict(usi=50)
        data = dict(suite_hash=digest(spec), benchmark_id=spec["benchmark_id"],
                    scaffold_id=spec["scaffold_id"], data_kind="empirical", candidate_id="test",
                    budget_track="opaque_compute", runs=[
                        dict(case, status="completed", population=spec["population_by_seed"][str(case["seed"])],
                             scores={m: scores[i] for m in spec["domains"][case["domain"]]["metrics"]})
                        for i, case in enumerate(spec["cases"])
                    ])
        write_json(folder / record["id"] / "studies" / study["id"] / "results.json", data)
    write_json(folder / record["id"] / "campaign.json", record)


@pytest.fixture
def campaigns(tmp_path):
    plan = small_plan()
    plan["cases"].append(dict(plan["cases"][0], seed=101))
    source = prepare(plan, {}, ["reciprocal"], [], "Smoke", mode="smoke")
    target = prepare(plan, {"model": MODEL}, [], [], "Model", mode="full")
    source.update(id="a" * 24, split="development")
    target.update(id="b" * 24, split="development")
    folder = tmp_path / "research" / "campaigns"
    completed(source, folder, [0.25])
    completed(target, folder, [0.75, 0.1])
    return source, target, folder


def test_shared_cases_exclude_unmatched_model_cases_and_single_seed_ci(campaigns):
    source, target, folder = campaigns
    row = compare_smoke(source, target, folder)["rows"][0]
    assert row["cases"] == 1 and row["model_cases"] == 2
    assert row["delta"] == 50 and row["model_score"] == 75
    assert row["wins"] == 1 and row["losses"] == 0
    assert row["ci95"] is None and row["verdict"] == "descriptive"


@pytest.mark.parametrize("change", ["weights", "peers", "runtime", "split"])
def test_incompatible_conditions_are_rejected(campaigns, change):
    source, target, folder = campaigns
    target = deepcopy(target)
    if change == "weights":
        target["plan"]["domains"]["D1"]["metrics"]["D1.prediction"] = 0.9
    elif change == "peers":
        target["plan"]["peers"][0] = "passive"
    else:
        target[change] = "different"
    assert not compatible(source, target)
    with pytest.raises(ValueError, match="share runtime"):
        compare_smoke(source, target, folder)


def test_failed_model_studies_are_excluded(campaigns):
    source, target, folder = campaigns
    target["studies"][0]["status"] = "failed"
    assert compare_smoke(source, target, folder)["rows"] == []


def test_roles_do_not_multiply_independent_seed_count():
    left = {("D1", "standard", 1, str(role), 0): 0.8 for role in range(20)}
    right = {key: 0.3 for key in left}
    result = paired_summary(left, right)
    assert result["seeds"] == 1 and result["ci95"] is None
    left[("D1", "standard", 2, "0", 0)] = 0.4
    right[("D1", "standard", 2, "0", 0)] = 0.3
    result = paired_summary(left, right, resamples=50)
    assert result["ci95"] is not None and result["verdict"] == "inconclusive"


async def test_comparison_endpoint_selects_compatible_smoke(campaigns, tmp_path):
    source, target, _ = campaigns
    router = build_research_router(tmp_path, tmp_path / "models", tmp_path / "recipes", "standard-v1")
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/recipe-lab/evaluations/{target['id']}/smoke-comparison")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["source_id"] == source["id"]
        assert result["rows"][0]["delta"] == 50
        response = await client.get(f"/api/recipe-lab/evaluations/{target['id']}/smoke-comparison?reference={target['id']}")
        assert response.status_code == 422
    await router.shutdown_tasks()


def test_same_family_label_does_not_pair_different_resolved_layers(campaigns):
    source, target, folder = campaigns
    for record in [source, target]:
        suite = record["studies"][0]["execution"]["suite"]
        suite["cases"][0]["layers"] = dict(resolve_layers(suite["cases"][0]["layers"]), family="same-family")
    target["studies"][0]["execution"]["suite"]["cases"][0]["layers"]["noise"]["execution_error"] += 1
    completed(source, folder, [0.25])
    completed(target, folder, [0.75, 0.1])
    assert compatible(source, target)
    assert compare_smoke(source, target, folder)["rows"] == []


def test_missing_runtime_cannot_establish_compatibility(campaigns):
    source, target, _ = campaigns
    source.pop("runtime")
    target.pop("runtime")
    assert not compatible(source, target)


def test_paired_summary_uses_only_intersection():
    shared = ("D1", "standard", 1, "advantaged", 0)
    extra = ("D1", "standard", 2, "advantaged", 0)
    result = paired_summary({shared: 0.75, extra: 0.0}, {shared: 0.25})
    assert result["cases"] == 1 and result["model_score"] == 75
    assert result["delta"] == 50
    with pytest.raises(ValueError, match="No shared cases"):
        paired_summary({extra: 0.0}, {shared: 0.25})
