from copy import deepcopy

import pytest

from unimatrix.benchmark.recipes import RecipeRepository, scoring_spec
from unimatrix.evaluation.scoring import canonical_hash, case_key, summarize
from unimatrix.evaluation.stability import ranking_stability


def panel(seeds=4):
    recipe = RecipeRepository().get("standard-v1")
    recipe["domains"] = {d: recipe["domains"][d] for d in ("D1", "D3")}
    recipe["cases"] = [
        dict(domain=d, layers="standard", seed=s, role=r, replicate=0)
        for d in recipe["domains"]
        for s in range(seeds)
        for r in ("advantaged", "disadvantaged")
    ]
    spec = scoring_spec(recipe)
    spec["bootstrap"]["resamples"] = 500

    def data(name, value):
        return dict(
            candidate_id=name,
            data_kind="synthetic",
            scaffold_id=spec["scaffold_id"],
            benchmark_id=spec["benchmark_id"],
            suite_hash=canonical_hash(spec),
            budget_track="accounted_compute",
            runs=[
                dict(
                    c,
                    status="completed",
                    population=spec["population_by_seed"][str(c["seed"])],
                    scores={m: value(c) for m in spec["domains"][c["domain"]]["metrics"]},
                )
                for c in spec["cases"]
            ],
        )

    refs = {case_key(c): (0.2, 0.6) for c in spec["cases"]}
    return spec, refs, data


def test_reference_gain_is_signed_uncapped_and_complete():
    spec, refs, data = panel()
    high = summarize(data("high", lambda c: 1.0), spec, refs)
    low = summarize(data("low", lambda c: 0.0), spec, refs)
    assert high["rating"] == pytest.approx(2)
    assert high["rating_ci95"][0] < 2 < high["rating_ci95"][1]
    assert low["rating"] == pytest.approx(-0.5)
    refs.pop(next(iter(refs)))
    incomplete = summarize(data("high", lambda c: 1.0), spec, refs)
    assert incomplete["rating"] is None and incomplete["rating_ci95"] is None


def test_known_order_ties_and_joint_seed_resampling():
    spec, refs, data = panel()
    a = data("a", lambda c: 0.6 + 0.01 * c["seed"])
    b = data("b", lambda c: 0.3 + 0.01 * c["seed"])
    result = ranking_stability([a, b], spec, refs)
    assert result["inferential"]
    assert result["exact_order_fraction"] == 1
    assert [r["supported_rank_range"] for r in result["ranking"]] == [[1, 2], [1, 2]]
    assert result["comparisons"][0]["simultaneous_ci95"][0] < 0 < result["comparisons"][0]["simultaneous_ci95"][1]
    tie = deepcopy(a)
    tie["candidate_id"] = "tie"
    result = ranking_stability([a, tie], spec, refs)
    assert all(r["rank"] == [1, 2] and r["top_share"] == 0.5 for r in result["ranking"])
    assert result["comparisons"][0]["verdict"] == "unresolved"
    assert result["comparisons"][0]["profile_status"] == "unresolved"
    assert all(
        r["identical_observed_scores"] for r in result["comparisons"][0]["domain_comparisons"]
    )


def test_crossing_domains_and_insufficient_seeds_are_not_stability():
    spec, refs, data = panel(64)
    a = data("a", lambda c: 0.9 if c["domain"] == "D1" else 0.1)
    b = data("b", lambda c: 0.1 if c["domain"] == "D1" else 0.9)
    result = ranking_stability([a, b], spec)
    comparison = result["comparisons"][0]
    assert comparison["verdict"] == "unresolved"
    assert comparison["profile_status"] == "tradeoff"
    assert comparison["aggregation"]["cancelled_fraction"] == pytest.approx(1)
    assert all(r["supported_rank_range"] == [1, 2] for r in result["ranking"])
    assert [(r["domain"], r["verdict"]) for r in comparison["domain_comparisons"]] == [
        ("D1", "left_higher"),
        ("D3", "right_higher"),
    ]
    assert result["domain_interval_family"]["comparisons"] == 2
    assert result["domain_interval_family"]["joint_family_size"] == 12
    assert result["leave_one_out"]["domain"]["D1"]["a"] == [2, 2]
    assert result["leave_one_out"]["domain"]["D3"]["a"] == [1, 1]
    spec, refs, data = panel(1)
    result = ranking_stability([data("a", lambda c: 0.9), data("b", lambda c: 0.1)], spec, refs)
    assert not result["inferential"]
    assert result["comparisons"][0]["profile_status"] == "insufficient_seeds"
    assert result["comparisons"][0]["simultaneous_ci95"] is None
    assert result["comparisons"][0]["raw_metric"]["simultaneous_ci95"] is None


def test_raw_aggregate_can_resolve_a_large_gap_while_calibrated_order_stays_open():
    spec, refs, data = panel(64)
    panels = [data("high", lambda c: 1.0), data("low", lambda c: 0.0)]
    result = ranking_stability(panels, spec, refs)
    pair = result["comparisons"][0]
    assert pair["raw_metric"]["delta"] == pytest.approx(1)
    assert pair["raw_metric"]["verdict"] == "left_higher"
    assert pair["raw_metric"]["simultaneous_ci95"][0] > 0
    assert pair["verdict"] == "unresolved"
    assert result["domain_interval_family"]["metric_families"] == [
        "reference_gain", "raw_metric", "left_strict_win", "right_strict_win"
    ]
    raw = ranking_stability(panels, spec)
    assert pair["raw_metric"]["simultaneous_ci95"] == raw["comparisons"][0][
        "simultaneous_ci95"
    ]


def test_ranking_rejects_incomplete_and_mismatched_panels():
    spec, refs, data = panel()
    a, b = data("a", lambda c: 0.9), data("b", lambda c: 0.1)
    b["budget_track"] = "opaque_compute"
    with pytest.raises(ValueError, match="Mismatched"):
        ranking_stability([a, b], spec, refs)
    b["budget_track"] = a["budget_track"]
    b["runs"].pop()
    with pytest.raises(ValueError, match="Missing"):
        ranking_stability([a, b], spec, refs)


def test_leaderboard_uses_dominance_and_excludes_legacy(monkeypatch, tmp_path):
    from unimatrix.benchmark.service import BenchmarkService

    service = BenchmarkService(tmp_path, None)

    def row(name, raw, rating=None):
        report = dict(usi=raw)
        if rating is not None:
            report.update(rating=rating, scoring_version="reference-gain-v1")
        return dict(
            candidate_id=name,
            cohort="same",
            budget_track="accounted_compute",
            status="completed",
            created_at=name,
            report=report,
        )

    monkeypatch.setattr(
        service, "runs", lambda: [row("a", 90, 0.7), row("b", 70, 1.3), row("legacy", 100)]
    )
    assert [r["candidate_id"] for r in service._eligible("same", "accounted_compute")] == ["a", "b"]
    assert not service.stability("empty", "accounted_compute")["available"]
    monkeypatch.setattr(
        service,
        "stability",
        lambda *args: {
            "primary_order": {
                "ranking": [
                    dict(system="a", front=1, dominates=[], dominated_by=[]),
                    dict(system="b", front=1, dominates=[], dominated_by=[]),
                ]
            }
        },
    )
    rows = service.leaderboard("same", "accounted_compute")
    assert [r["candidate_id"] for r in rows] == ["a", "b"]
    assert all(r["dominance"]["front"] == 1 for r in rows)


def test_complete_exports_recompute_reference_gain_and_reject_changed_anchors():
    from unimatrix.evaluation.reports import compare_exports

    spec, refs, data = panel()
    anchors = [dict(case=list(k), floor=v[0], anchor=v[1]) for k, v in refs.items()]
    left = dict(suite=spec, results=data("a", lambda c: 0.9), references=anchors)
    right = dict(suite=spec, results=data("b", lambda c: 0.6), references=anchors)
    report = compare_exports(left, right)
    assert report["left"]["rating"] == pytest.approx(1.75)
    assert report["comparison"]["reference_gain"]["delta"] == pytest.approx(0.75)
    right = deepcopy(right)
    right["references"][0]["anchor"] = 0.7
    with pytest.raises(ValueError, match="reference anchors"):
        compare_exports(left, right)


def test_primary_interval_does_not_infer_zero_population_variance_from_cancellation():
    spec, refs, data = panel(8)
    a = data("a", lambda c: 0.5 + (0.03 * c["seed"] if c["domain"] == "D1" else -0.03 * c["seed"]))
    result = summarize(a, spec, refs)
    assert result["rating"] == pytest.approx(0.75)
    assert result["rating_ci95"][0] < 0.75 < result["rating_ci95"][1]
