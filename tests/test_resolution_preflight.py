"""Prospective resolution agrees with the real interval and never starts a futile study."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

from unimatrix.benchmark.recipes import RecipeRepository, scoring_spec
from unimatrix.core.ids import digest
from unimatrix.evaluation.stability import ranking_stability
from unimatrix.research.measurement_quality import balanced_bank, main
from unimatrix.research.resolution import resolution_plan


def bank(n=8):
    return balanced_bank(RecipeRepository().get("standard-v1"), list(range(50000, 50000+n)), ticks=72)


def test_even_perfect_model_separation_cannot_pass_the_eight_seed_rule():
    recipe = bank()
    recipe["bootstrap"]["resamples"] = 100
    planned = resolution_plan(recipe)
    suite = scoring_spec(recipe)
    panels = []
    for value in [0.0, 1.0]:
        panels.append(dict(candidate_id=str(value), data_kind="synthetic", suite_hash=digest(suite),
                           scaffold_id=suite["scaffold_id"], benchmark_id=suite["benchmark_id"],
                           budget_track="accounted_compute", runs=[dict(c, status="completed", population="P0",
                           scores=dict.fromkeys(recipe["domains"][c["domain"]]["metrics"], value)) for c in recipe["cases"]]))
    actual = ranking_stability(panels, suite)
    assert actual["domain_interval_family"]["joint_family_size"] == planned["joint_family_size"] == 36
    assert not planned["direction_confirmation_possible"]
    assert not planned["casewise_unanimity_certificate_possible"]
    assert not planned["preflight_passed"]
    assert "direction_rule_unresolvable" in planned["issues"]
    for row in actual["comparisons"][0]["domain_comparisons"]:
        expected = planned["domains"][row["domain"]]["interval_at_maximum_raw_gap"]
        assert row["simultaneous_ci95"] == pytest.approx([-expected[1], -expected[0]])
        assert row["verdict"] == "unresolved"


def test_repeats_and_more_ticks_do_not_invent_independent_clusters():
    original = bank()
    changed = deepcopy(original)
    changed["cases"] += [dict(c, replicate=1) for c in original["cases"]]
    a, b = resolution_plan(original), resolution_plan(changed)
    assert a["domains"] == b["domains"]
    long = balanced_bank(RecipeRepository().get("standard-v1"), list(range(50000, 50008)), ticks=240)
    assert resolution_plan(long)["domains"] == a["domains"]


def test_complete_pair_family_and_time_are_both_checked():
    recipe = bank(64)
    two = resolution_plan(recipe, systems=2, target_gap=.5)
    many = resolution_plan(recipe, systems=12, target_gap=.5)
    assert two["preflight_passed"]
    assert not many["preflight_passed"]
    assert many["joint_family_size"] == 2376
    assert many["casewise_unanimity_certificate_possible"]
    expensive = resolution_plan(recipe, target_gap=.5, seconds_per_decision=30)
    assert expensive["direction_confirmation_possible"]
    assert not expensive["preflight_passed"]
    assert "projected_wall_budget_exceeded" in expensive["issues"]
    assert expensive["provider_calls"] == 0


def test_validation_twelve_model_casewise_unanimity_is_possible_but_mean_gap_is_not():
    plan = resolution_plan(
        RecipeRepository().get("validation-v1"), systems=12, target_gap=0.05
    )
    assert plan["joint_family_size"] == 2376
    assert not plan["direction_confirmation_possible"]
    assert plan["casewise_unanimity_certificate_possible"]
    assert all(
        row["clusters"] == 16
        and row["family_adjusted_p_upper_bound_if_unanimous"] == 2376 / 65536
        for row in plan["casewise_domains"].values()
    )


@pytest.mark.parametrize("args", [dict(systems=True), dict(systems=1), dict(systems=1001), dict(target_gap=True), dict(target_gap=0), dict(target_gap=float("nan")), dict(target_gap=1.01)])
def test_invalid_design_parameters_are_rejected(args):
    with pytest.raises(ValueError):
        resolution_plan(bank(), **args)


def test_cli_writes_failure_evidence_without_starting_a_study(tmp_path):
    recipe = tmp_path / "recipe.json"
    output = tmp_path / "plan.json"
    recipe.write_text(json.dumps(bank()))
    assert main(["design", "--recipe", str(recipe), "--systems", "2", "--output", str(output)]) == 2
    report = json.loads(output.read_text())
    assert not report["direction_confirmation_possible"] and report["provider_calls"] == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plan.json", "recipe.json"]


def test_default_control_holdout_stops_before_creating_workers(tmp_path, monkeypatch):
    tool_dir = Path(__file__).parent / "tools"
    monkeypatch.syspath_prepend(str(tool_dir))
    spec = importlib.util.spec_from_file_location("holdout_preflight_test", tool_dir / "profile_holdout.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("sys.argv", ["profile_holdout.py", "--output", str(tmp_path)])

    def forbidden(*args, **kwargs):
        raise AssertionError("An infeasible confirmation must not create workers")

    monkeypatch.setattr(module.concurrent.futures, "ProcessPoolExecutor", forbidden)
    with pytest.raises(SystemExit, match="no episodes started"):
        module.main()
    assert {p.name for p in tmp_path.iterdir()} == {"design-preflight.json"}
