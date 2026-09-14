"""Release evidence is assessed explicitly, never inferred from passing unit tests."""

import math


def audit(evidence):
    checks = {
        "six_scripted_baselines": evidence.get("scripted_baselines_complete") is True,
        "three_distinct_llm_systems": len(set(evidence.get("llm_fingerprints", []))) >= 3,
        "two_independent_reviews": len(set(evidence.get("review_ids", []))) >= 2,
        "frozen_reference_pool": len(set(evidence.get("reference_fingerprints", []))) == 2,
        "golden_replay_identical": evidence.get("golden_replay_identical") is True,
        "zero_unauthorized_access": evidence.get("unauthorized_accesses") == 0,
        "all_instances_feasible": evidence.get("all_instances_feasible") is True,
        "infrastructure_completion": evidence.get("completion_rate", 0) >= 0.95,
        "no_saturated_metric": evidence.get("max_saturation", 1) <= 0.8,
        "precision": evidence.get("median_ci_halfwidth", math.inf) <= 5,
        "communication_ablation": evidence.get("communication_ablation_passed") is True,
        "memory_ablation": evidence.get("memory_ablation_passed") is True,
        "gaming_audit": evidence.get("gaming_audit_passed") is True,
        "heldout_disjoint": evidence.get("heldout_disjoint") is True,
    }
    # This implementation is still experimental: evidence collection does not
    # automatically authorize a release or change a frozen suite's version.
    return dict(
        release_status="draft",
        certified=False,
        checks=checks,
        missing=[key for key, value in checks.items() if not value],
        ready_for_review=all(checks.values()),
    )
