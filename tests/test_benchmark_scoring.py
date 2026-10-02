import json
from copy import deepcopy
from itertools import product
from pathlib import Path

import pytest

from unimatrix.evaluation.scoring import canonical_hash, compare, summarize, validate
from unimatrix.scenarios.layers import PRESETS

BUNDLE = Path(__file__).parents[1] / "tests/fixtures/blueprint"


def fixture():
    """The bundle numbers its complexity strata and crosses its axes; the scorer reads cases."""
    data = json.loads((BUNDLE / "examples/synthetic-results.json").read_text())
    manifest = json.loads((BUNDLE / "examples/benchmark-core.json").read_text())
    strata = dict(zip(manifest.pop("levels"), PRESETS))
    for row in data["runs"]:
        row["layers"] = strata[row.pop("level")]
    manifest["cases"] = [
        dict(domain=d, layers=c, seed=s, role=r, replicate=n)
        for d, c, s, r, n in product(
            manifest["domains"],
            strata.values(),
            manifest["seeds"],
            manifest["roles"],
            manifest["replicates"],
        )
    ]
    data["suite_hash"] = canonical_hash(manifest)
    return data, manifest


def test_reference_matches_supplied_golden_report():
    data, manifest = fixture()
    expected = json.loads((BUNDLE / "examples/synthetic-report.json").read_text())
    result = summarize(data, manifest)
    assert result["usi"] == expected["usi"] == 69.35
    assert result["ci95"][0] < expected["ci95"][0] < expected["ci95"][1] < result["ci95"][1]
    assert not result["certified"]


def test_paired_identity_and_ordering():
    data, manifest = fixture()
    result = compare(data, data, manifest)
    assert result["paired_delta"] == 0 and result["paired_ci95"][0] < 0 < result["paired_ci95"][1]
    shuffled = deepcopy(data)
    shuffled["runs"].reverse()
    assert summarize(shuffled, manifest) == summarize(data, manifest)


@pytest.mark.parametrize(
    "failure", ["missing", "duplicate", "wrong_pool", "nan", "incomplete", "hash"]
)
def test_invalid_suite_rejected(failure):
    data, manifest = fixture()
    if failure == "missing":
        data["runs"].pop()
    elif failure == "duplicate":
        data["runs"].append(data["runs"][0])
    elif failure == "wrong_pool":
        data["runs"][0]["population"] = "wrong"
    elif failure == "nan":
        data["runs"][0]["scores"][next(iter(data["runs"][0]["scores"]))] = float("nan")
    elif failure == "incomplete":
        data["runs"][0]["status"] = "infra_failed"
    else:
        data["suite_hash"] = "wrong"
    with pytest.raises(ValueError):
        validate(data, manifest)


def test_export_comparison_recomputes_forged_summary_and_rejects_missing_rows():
    from unimatrix.evaluation.reports import compare_exports

    data, spec = fixture()
    exported = dict(results=data, suite=spec, report=dict(usi=999))
    result = compare_exports(exported, exported)
    assert result["left"]["usi"] == 69.35
    assert result["comparison"]["paired_ci95"][0] < 0 < result["comparison"]["paired_ci95"][1]
    incomplete = deepcopy(exported)
    incomplete["results"]["runs"].pop()
    with pytest.raises(ValueError, match="Missing"):
        compare_exports(exported, incomplete)


def test_comparison_standard_error_keeps_roles_in_the_same_seed_cluster():
    from unimatrix.benchmark.recipes import RecipeRepository, scoring_spec

    recipe = RecipeRepository().get("standard-v1")
    recipe["domains"] = {"D1": recipe["domains"]["D1"]}
    recipe["cases"] = [
        dict(domain="D1", layers="standard", seed=seed, role=role, replicate=0)
        for seed in (10, 11)
        for role in ("advantaged", "disadvantaged")
    ]
    spec = scoring_spec(recipe)

    def data(system, active):
        return dict(
            benchmark_id=spec["benchmark_id"],
            scaffold_id=spec["scaffold_id"],
            suite_hash=canonical_hash(spec),
            candidate_id=system,
            data_kind="empirical",
            budget_track="accounted_compute",
            runs=[
                dict(
                    c,
                    population="P0",
                    status="completed",
                    scores={
                        m: float(active and c["seed"] == 11)
                        for m in spec["domains"]["D1"]["metrics"]
                    },
                )
                for c in recipe["cases"]
            ],
        )

    result = compare(data("left", True), data("right", False), spec)
    assert result["matched_episodes"] == 4
    assert result["matched_seed_clusters"] == 2
    assert result["paired_se"] == 50
