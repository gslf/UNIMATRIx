from copy import deepcopy

import pytest

from unimatrix.benchmark.plans import PlanRepository, bind_candidate, validate_plan
from unimatrix.core.ids import digest


def test_candidates_share_all_cases_peers_and_scoring_rules():
    spec = PlanRepository().get("standard-v1")
    a = bind_candidate(spec, "passive")
    b = bind_candidate(spec, "reciprocal")
    assert len(a["episodes"]) == len(spec["cases"]) == 192
    assert a["suite"] == b["suite"]
    for left, right, case in zip(a["episodes"], b["episodes"], spec["cases"]):
        for key in ("domain", "level", "seed", "role", "replicate"):
            assert left[key] == right[key] == case[key]
        assert left["focal_slot"] == right["focal_slot"]
        assert left["suite_hash"] == right["suite_hash"]
        peers = [s for s in left["slots"] if s != left["focal_slot"]]
        assert [left["policies"][s] for s in peers] == spec["peers"]
        assert [right["policies"][s] for s in peers] == spec["peers"]


def test_changed_rules_create_a_different_leaderboard_identity():
    spec = PlanRepository().get("compact-v1")
    first = bind_candidate(spec, "passive")
    changed = deepcopy(spec)
    changed["cases"][0]["seed"] = 123
    second = bind_candidate(changed, "passive")
    assert digest(first["suite"]) != digest(second["suite"])
    changed = deepcopy(spec)
    changed["domains"]["D1"]["metrics"].update({"D1.prediction": 0.4, "D1.decision": 0.35})
    assert digest(bind_candidate(changed, "passive")["suite"]) != digest(first["suite"])


def test_invalid_or_duplicate_cases_and_unsupported_budgets_are_rejected():
    spec = PlanRepository().get("compact-v1")
    duplicate = deepcopy(spec)
    duplicate["cases"].append(duplicate["cases"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_plan(duplicate)
    changed = deepcopy(spec)
    changed["budgets"]["generation_tokens_per_decision"] = 8192
    with pytest.raises(ValueError, match="budgets"):
        validate_plan(changed)
    changed = deepcopy(spec)
    changed["cases"][0]["level"] = 4
    with pytest.raises(ValueError, match="Invalid benchmark case"):
        validate_plan(changed)


def test_connection_and_credentials_do_not_create_a_new_competitor():
    spec = PlanRepository().get("compact-v1")
    model = dict(
        model="candidate",
        snapshot="v1",
        endpoint="http://localhost:1",
        context_bytes_verified=24000,
        budget_track="opaque_compute",
        temperature=0,
    )
    same = dict(model, endpoint="http://localhost:2", api_key_env="NEW_KEY")
    changed = dict(model, temperature=1)
    assert bind_candidate(spec, model)["candidate_id"] == bind_candidate(spec, same)["candidate_id"]
    assert (
        bind_candidate(spec, model)["candidate_id"] != bind_candidate(spec, changed)["candidate_id"]
    )
