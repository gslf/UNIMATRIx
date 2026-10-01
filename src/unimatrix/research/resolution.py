"""Prospective feasibility of the registered raw-score comparison rule.

Uses design weights, never observed model scores. A possible rejection is not a
power guarantee; the distribution-free power count is labelled separately.
"""

import math

from ..benchmark.duration import estimate
from ..core.ids import digest
from ..evaluation.casewise import METHOD as CASEWISE_METHOD
from ..evaluation.casewise import all_win_certificate
from ..evaluation.uncertainty import METHOD, required_seeds, seed_estimator, weighted_interval
from .measurement_quality import audit_recipe
from .provenance import analysis_provenance


def resolution_plan(recipe, *, systems=2, target_gap=0.05, seconds_per_decision=None):
    if type(systems) is not int or not 2 <= systems <= 1000:
        raise ValueError("systems_must_be_an_integer_between_2_and_1000")
    if type(target_gap) not in (int, float) or not math.isfinite(target_gap) or not 0 < target_gap <= 1:
        raise ValueError("raw_target_gap_must_be_in_0_1")
    coverage = audit_recipe(recipe)
    from ..scenarios.layers import layers_key

    cases = {(c["domain"], layers_key(c["layers"]), c["seed"], c["role"], c["replicate"]): 0.0
             for c in recipe["cases"]}
    domains = sorted(recipe["domains"])
    family_size = 4 * math.comb(systems, 2) * (1 + len(domains))
    compatible = coverage["complete_balanced_design"]
    sufficient_clusters = coverage["minimum_stratum_clusters"] >= 4

    def one(selected=None):
        _, weights = seed_estimator(cases, selected)
        extreme = weighted_interval(dict.fromkeys(weights, 1.0), weights, (-1, 1), comparisons=family_size)
        target = weighted_interval(dict.fromkeys(weights, float(target_gap)), weights, (-1, 1), comparisons=family_size)
        return dict(seed_clusters=len(weights), effective_seed_clusters=1 / sum(w*w for w in weights.values()),
                    interval_at_maximum_raw_gap=extreme, interval_at_target_raw_gap=target,
                    any_direction_possible=extreme[0] > 0, target_observed_gap_resolvable=target[0] > 0)

    per_domain = {d: one([d]) for d in domains}
    aggregate = one()
    casewise_domains = {}
    for domain in domains:
        _, weights = seed_estimator(cases, [domain])
        certificate = all_win_certificate(
            dict.fromkeys(weights, 1.0), weights, comparisons=family_size,
            eligible=sufficient_clusters,
        )
        casewise_domains[domain] = dict(
            clusters=certificate["clusters"],
            equal_cluster_weights=certificate["equal_cluster_weights"],
            family_adjusted_p_upper_bound_if_unanimous=certificate[
                "family_adjusted_p_upper_bound"
            ],
            unanimity_certificate_possible=certificate["verdict"] == "left_majority",
        )
    duration = estimate(recipe, seconds_per_decision) if seconds_per_decision is not None else None
    issues = []
    if not compatible:
        issues.append("unbalanced_or_incomplete_design")
    if not sufficient_clusters:
        issues.append("insufficient_seed_clusters")
    if not all(r["any_direction_possible"] for r in per_domain.values()):
        issues.append("direction_rule_unresolvable")
    if not all(r["target_observed_gap_resolvable"] for r in per_domain.values()):
        issues.append("target_gap_below_resolution")
    if duration is not None and not duration["fits_budget"]:
        issues.append("projected_wall_budget_exceeded")
    return dict(preflight_passed=not issues, issues=issues, format="unimatrix.resolution-plan.v4", recipe=recipe["id"], recipe_sha256=digest(recipe),
                analysis_provenance=analysis_provenance("unimatrix.research.resolution.resolution_plan"),
                method=METHOD, systems=systems, alpha=0.05, joint_family_size=family_size,
                target_raw_gap=float(target_gap), coverage=coverage, aggregate=aggregate, domains=per_domain,
                casewise_method=CASEWISE_METHOD, casewise_domains=casewise_domains,
                casewise_unanimity_certificate_possible=compatible and all(
                    row["unanimity_certificate_possible"] for row in casewise_domains.values()
                ),
                ranking_design_compatible=compatible, minimum_cluster_count_met=sufficient_clusters,
                direction_confirmation_possible=compatible and sufficient_clusters and all(
                    r["any_direction_possible"] for r in per_domain.values()),
                target_observed_gap_resolvable=compatible and sufficient_clusters and all(
                    r["target_observed_gap_resolvable"] for r in per_domain.values()),
                sufficient_equal_weight_clusters_for_80_percent_power=required_seeds(
                    target_gap, comparisons=family_size),
                duration=duration, provider_calls=0,
                scope="Prospective raw-score paired-mean intervals with independent seed clusters and the joint "
                      "raw/reference-gain aggregate/domain and casewise strict-win family. Possibility at an observed gap is not power or evidence of an effect. "
                      "The separate casewise flag asks only whether unanimous strict wins could pass; it does "
                      "not predict that they will occur or resolve the target mean gap. "
                      "The power count is a conservative sufficient condition for equal weights, not a minimum. "
                      "Duration is a planning estimate, not a completed measurement.")
