"""Independent-cell design checks against the actual scoring/interval machinery."""

from copy import deepcopy
from itertools import product
from math import comb
from statistics import fmean

import pytest

from unimatrix.benchmark.recipes import RecipeRepository, scoring_spec
from unimatrix.core.ids import digest
from unimatrix.evaluation.sampling import sampling_layout
from unimatrix.evaluation.scoring import case_key, domain_means, validate
from unimatrix.evaluation.stability import ranking_stability
from unimatrix.evaluation.uncertainty import weighted_interval
from unimatrix.research.measurement_quality import audit_recipe, balanced_bank
from unimatrix.research.resolution import resolution_plan


def bank(n=8):
    return balanced_bank(RecipeRepository().get("standard-v1"), list(range(64000, 64000+n)),
                         ticks=72, sampling="independent")


def data(suite, name, value):
    return dict(candidate_id=name, data_kind="synthetic", suite_hash=digest(suite),
                scaffold_id=suite["scaffold_id"], benchmark_id=suite["benchmark_id"],
                budget_track="accounted_compute", runs=[dict(c, status="completed",
                population=suite["population_by_seed"][str(c["seed"])] ,
                scores=dict.fromkeys(suite["domains"][c["domain"]]["metrics"], value(c)))
                for c in suite["cases"]])


def test_distinct_worlds_keep_coverage_and_calls_and_reduce_design_radius():
    independent = bank()
    shared = balanced_bank(RecipeRepository().get("standard-v1"), list(range(64000, 64008)), ticks=72)
    assert independent == bank()
    a, b = audit_recipe(independent), audit_recipe(shared)
    assert a["fully_crossed"] and a["ranking_design_compatible"]
    assert a["sampling_design"] == "independent_cell_strata"
    assert a["minimum_stratum_clusters"] == 8
    assert a["independent_seed_clusters"] == 256
    assert b["independent_seed_clusters"] == 8
    assert a["candidate_decisions"] == b["candidate_decisions"] == 18432
    planned = resolution_plan(independent, systems=12, target_gap=1)
    original = resolution_plan(shared, systems=12, target_gap=1)
    assert planned["direction_confirmation_possible"]
    assert not original["direction_confirmation_possible"]
    assert planned["aggregate"]["effective_seed_clusters"] == 256
    assert planned["domains"]["D1"]["effective_seed_clusters"] == 32
    assert planned["casewise_unanimity_certificate_possible"]
    assert not original["casewise_unanimity_certificate_possible"]
    assert planned["aggregate"]["interval_at_maximum_raw_gap"][0] == pytest.approx(.7007554676956738)


def test_preflight_matches_the_full_twelve_model_comparison_family():
    recipe = bank()
    recipe["bootstrap"]["resamples"] = 100
    spec = scoring_spec(recipe)
    planned = resolution_plan(recipe, systems=12, target_gap=1)
    actual = ranking_stability([data(spec, f"m{i:02}", lambda c, i=i: i/11) for i in range(12)], spec)
    assert actual["minimum_stratum_clusters"] == 8
    assert actual["sampling_design"] == "independent_cell_strata"
    assert actual["domain_interval_family"]["joint_family_size"] == planned["joint_family_size"] == 2376
    pair = next(p for p in actual["comparisons"] if p["left"] == "m00" and p["right"] == "m11")
    assert pair["verdict"] == "right_higher"
    assert pair["casewise_profile_status"] == "right_majority_all_domains"
    for row in pair["domain_comparisons"]:
        expected = planned["domains"][row["domain"]]["interval_at_maximum_raw_gap"]
        assert row["simultaneous_ci95"] == pytest.approx([-expected[1], -expected[0]])


def test_bootstrap_and_leave_one_out_preserve_stratum_weights_and_model_pairing():
    spec = scoring_spec(bank(4))
    spec["bootstrap"]["resamples"] = 100

    def value(c):
        return .1 + .15 * (c["role"] == "advantaged") + .05 * int(c["domain"][1])
    a = data(spec, "a", value)
    b = data(spec, "b", lambda c: value(c) - .01)
    result = ranking_stability([a, b], spec)
    expected = domain_means(validate(a, spec), spec)
    assert result["ranking"][0]["score"] == pytest.approx(fmean(expected.values()))
    assert result["exact_order_fraction"] == 1
    assert result["comparisons"][0]["bootstrap_win_fraction"] == 1
    assert all(r == {"a": [1, 1], "b": [2, 2]} for r in result["leave_one_out"]["seed"].values())


def test_repeats_are_clustered_and_minimum_is_per_stratum():
    recipe = bank(3)
    repeated = deepcopy(recipe)
    repeated["cases"] += [dict(c, replicate=1) for c in recipe["cases"]]
    a, b = resolution_plan(recipe), resolution_plan(repeated)
    assert a["domains"] == b["domains"]
    assert a["aggregate"] == b["aggregate"]
    assert a["coverage"]["independent_seed_clusters"] == 96
    assert not a["minimum_cluster_count_met"]
    spec = scoring_spec(repeated)
    spec["bootstrap"]["resamples"] = 100
    actual = ranking_stability([data(spec, "a", lambda c: 1), data(spec, "b", lambda c: 0)], spec)
    assert not actual["inferential"]
    assert actual["minimum_stratum_clusters"] == 3


@pytest.mark.parametrize("fault", ["partial_overlap", "missing_cell", "unequal_repeats"])
def test_partial_overlap_or_unbalanced_cells_are_rejected(fault):
    recipe = bank(4)
    if fault == "partial_overlap":
        recipe["cases"][1]["seed"] = recipe["cases"][0]["seed"]
    elif fault == "missing_cell":
        recipe["cases"].pop()
    else:
        recipe["cases"].append(dict(recipe["cases"][0], replicate=1))
    assert not audit_recipe(recipe)["ranking_design_compatible"]
    assert not resolution_plan(recipe)["preflight_passed"]
    spec = scoring_spec(recipe)
    with pytest.raises(ValueError, match="balanced"):
        ranking_stability([data(spec, "a", lambda c: 1), data(spec, "b", lambda c: 0)], spec)


def test_prefixes_keep_whole_strata_and_no_seed_is_reinterpreted_as_a_repeat():
    spec = scoring_spec(bank(3))
    layout = sampling_layout({case_key(c) for c in spec["cases"]}, spec["population_by_seed"])
    order = [c["seed"] for c in spec["cases"]]
    assert list(layout.complete_prefixes(order)) == [32, 64, 96]
    with pytest.raises(ValueError, match="seed_order"):
        list(layout.complete_prefixes(order + [order[0]]))
    grouped = [s for pool in layout.pools.values() for s in pool]
    assert list(layout.complete_prefixes(grouped)) == [96]


def test_exact_heterogeneous_bernoulli_coverage_of_weighted_rule():


    probabilities = [.1, .3, .6, .9]
    stratum_weights = [.1, .2, .3, .4]
    n = 8
    weights = {i*n+j: w/n for i, w in enumerate(stratum_weights) for j in range(n)}
    truth = sum(w*p for w, p in zip(stratum_weights, probabilities))
    failure = 0.0
    for counts in product(range(n+1), repeat=4):
        mass = 1.0
        for k, p in zip(counts, probabilities):
            mass *= comb(n, k) * p**k * (1-p)**(n-k)
        values = {i*n+j: int(j < k) for i, k in enumerate(counts) for j in range(n)}
        ci = weighted_interval(values, weights, (0, 1), comparisons=2)
        mirror = weighted_interval({i: 1-x for i, x in values.items()}, weights, (0, 1), comparisons=2)
        failure += mass * (not ci[0] <= truth <= ci[1] or not mirror[0] <= 1-truth <= mirror[1])
    assert 0 < failure <= .05


def test_shared_bank_still_requires_the_complete_replication_crossing():
    recipe = balanced_bank(RecipeRepository().get("standard-v1"), list(range(4)), ticks=72)
    recipe["cases"] += [dict(c, replicate=1) for c in recipe["cases"] if c["role"] == "advantaged"]
    audit = audit_recipe(recipe)
    assert audit["ranking_design_compatible"]
    assert not audit["complete_balanced_design"]
    assert not resolution_plan(recipe)["ranking_design_compatible"]
