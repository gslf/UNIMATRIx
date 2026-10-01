"""Matched ranking uncertainty; preserve seed clusters and fixed design strata."""

from collections import Counter
from itertools import combinations
from random import Random
from statistics import fmean

from ..research.provenance import analysis_provenance
from .aggregation import aggregation_audit
from .casewise import METHOD as CASEWISE_METHOD
from .casewise import all_win_certificate
from .dominance import dominance_order
from .sampling import sampling_layout
from .scoring import domain_means, normalized_scores, percentile, validate
from .uncertainty import METHOD, seed_estimator, seed_interval


def ranking_stability(datasets, manifest, references=None):
    """Complete matched panels only. Bootstrap frequencies are not Bayesian probabilities.

    Keep ties as rank ranges. Pair intervals use a Bonferroni family-wise adjustment;
    finite-sample concentration bounds control the aggregate + domain family jointly.
    """
    if len(datasets) < 2:
        raise ValueError("At least two systems are required")
    identities = [d["candidate_id"] for d in datasets]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate candidate identity")
    for field in ("data_kind", "suite_hash", "scaffold_id", "budget_track"):
        if len({d.get(field) for d in datasets}) != 1:
            raise ValueError(f"Mismatched {field}")
    raw_panels = {d["candidate_id"]: validate(d, manifest) for d in datasets}
    raw_means = {name: domain_means(values, manifest) for name, values in raw_panels.items()}
    panels = raw_panels
    if references is not None:
        panels = {name: normalized_scores(values, references) for name, values in panels.items()}
        if any(v is None for values in panels.values() for v in values.values()):
            raise ValueError("Complete nondegenerate references required")
    names = sorted(panels)
    keys = set(panels[names[0]])
    if any(set(p) != keys for p in panels.values()):
        raise ValueError("Unmatched panels")
    seeds = sorted({k[2] for k in keys})
    layout = sampling_layout(keys, manifest["population_by_seed"])
    pools = layout.pools
    domains = sorted(manifest["domains"])


    estimates = {name: {d: seed_estimator(panel, [d]) for d in domains}
                 for name, panel in panels.items()}
    domain_pools = {
        d: {pool: sum(estimates[names[0]][d][1].get(s, 0) for s in members)
            for pool, members in pools.items()
            if any(s in estimates[names[0]][d][1] for s in members)}
        for d in domains
    }

    def domain_aggregate(sample, selected_domains=domains):
        return {
            name: {
                d: sum(weight * fmean(estimates[name][d][0][s] for s in sample[pool])
                       for pool, weight in domain_pools[d].items())
                for d in selected_domains
            }
            for name in names
        }

    def aggregate(sample, selected_domains=domains):
        return {
            n: fmean(values.values())
            for n, values in domain_aggregate(sample, selected_domains).items()
        }

    def ranks(scores):
        return {
            n: [
                1 + sum(v > scores[n] for v in scores.values()),
                sum(v >= scores[n] for v in scores.values()),
            ]
            for n in names
        }

    domain_point = domain_aggregate(pools)
    point = aggregate(pools)
    point_ranks = ranks(point)
    count = manifest["bootstrap"]["resamples"]
    if count < 100:
        raise ValueError("At least 100 bootstrap draws required")
    rng = Random(manifest["bootstrap"]["seed"])
    draws = {n: [] for n in names}
    rank_draws = {n: [] for n in names}
    domain_draws = {n: {d: [] for d in domains} for n in names}
    top_credit = Counter()
    same_order = 0
    for _ in range(count):
        sample = {pool: rng.choices(members, k=len(members)) for pool, members in pools.items()}
        domain_scores = domain_aggregate(sample)
        scores = {n: fmean(values.values()) for n, values in domain_scores.items()}
        rr = ranks(scores)
        same_order += rr == point_ranks
        best = max(scores.values())
        winners = [n for n in names if scores[n] == best]
        for n in names:
            draws[n].append(scores[n])
            for d in domains:
                domain_draws[n][d].append(domain_scores[n][d])
            rank_draws[n].append(rr[n])
        for n in winners:
            top_credit[n] += 1 / len(winners)
    pairs = list(combinations(names, 2))
    family_size = len(pairs) * (1 + len(domains))
    # Reserve the second family for the bounded raw metric, including when it
    # is analysed separately from reference gain. Both archived views can then
    # be read together without silently doubling the family-wise error rate.
    # Two further families reserve both directional strict-win tests.
    joint_family_size = 4 * family_size
    enough = layout.minimum_clusters >= 4
    comparisons = []
    for a, b in pairs:
        deltas = [x - y for x, y in zip(draws[a], draws[b])]
        differences = {k: panels[a][k] - panels[b][k] for k in keys}
        ci = seed_interval(differences, references=references, paired=True,
                           comparisons=joint_family_size) if enough else None
        raw_differences = {
            k: raw_panels[a][k] - raw_panels[b][k] for k in keys
        }
        raw_ci = seed_interval(raw_differences, paired=True,
                               comparisons=joint_family_size) if enough else None
        verdict = (
            "insufficient_seeds"
            if not enough
            else ("left_higher" if ci[0] > 0 else "right_higher" if ci[1] < 0 else "unresolved")
        )


        domain_rows = []
        casewise_rows = []
        for domain in domains:
            domain_ci = (
                seed_interval(differences, references=references, paired=True, domains=[domain],
                              comparisons=joint_family_size) if enough else None
            )
            direction = (
                "insufficient_seeds"
                if not enough
                else (
                    "left_higher"
                    if domain_ci[0] > 0
                    else "right_higher"
                    if domain_ci[1] < 0
                    else "unresolved"
                )
            )
            domain_rows.append(
                dict(
                    domain=domain,
                    left_mean=domain_point[a][domain],
                    right_mean=domain_point[b][domain],
                    delta=domain_point[a][domain] - domain_point[b][domain],
                    simultaneous_ci95=domain_ci,
                    verdict=direction,
                    identical_observed_scores=all(
                        panels[a][k] == panels[b][k] for k in keys if k[0] == domain
                    ),
                )
            )
            clusters, weights = seed_estimator(raw_differences, [domain])
            casewise_rows.append(dict(
                domain=domain,
                **all_win_certificate(
                    clusters, weights, comparisons=joint_family_size, eligible=enough
                ),
            ))
        advantages = {
            direction: [r["domain"] for r in domain_rows if r["verdict"] == direction]
            for direction in ("left_higher", "right_higher")
        }
        profile_status = (
            "insufficient_seeds"
            if not enough
            else (
                "tradeoff"
                if all(advantages.values())
                else "left_strengths"
                if advantages["left_higher"]
                else "right_strengths"
                if advantages["right_higher"]
                else "unresolved"
            )
        )
        comparisons.append(
            dict(
                left=a,
                right=b,
                aggregation=aggregation_audit(
                    {d: domain_point[a][d] - domain_point[b][d] for d in domains}
                ),
                raw_aggregation=aggregation_audit(
                    {d: raw_means[a][d] - raw_means[b][d] for d in domains}
                ),
                raw_metric=dict(
                    delta=fmean(raw_means[a].values()) - fmean(raw_means[b].values()),
                    simultaneous_ci95=raw_ci,
                    verdict=(
                        "insufficient_seeds" if not enough else
                        "left_higher" if raw_ci[0] > 0 else
                        "right_higher" if raw_ci[1] < 0 else "unresolved"
                    ),
                ),
                profile_status=profile_status,
                domain_comparisons=domain_rows,
                casewise_domains=casewise_rows,
                casewise_profile_status=(
                    "left_majority_all_domains"
                    if all(r["verdict"] == "left_majority" for r in casewise_rows)
                    else "right_majority_all_domains"
                    if all(r["verdict"] == "right_majority" for r in casewise_rows)
                    else "unresolved"
                ),
                delta=point[a] - point[b],
                simultaneous_ci95=ci,
                verdict=verdict,
                bootstrap_win_fraction=fmean((v > 0) + 0.5 * (v == 0) for v in deltas),
            )
        )

    ahead = Counter()
    behind = Counter()
    for pair in comparisons:
        if pair["verdict"] in ("left_higher", "right_higher"):
            winner, loser = (
                (pair["left"], pair["right"])
                if pair["verdict"] == "left_higher"
                else (pair["right"], pair["left"])
            )
            ahead[winner] += 1
            behind[loser] += 1
    sensitivity = {}
    for axis, omissions in (("domain", domains), ("seed", seeds)):
        if len(omissions) > 1:
            sensitivity[axis] = {
                str(x): ranks(
                    aggregate(
                        {p: [s for s in members if s != x] for p, members in pools.items()}
                        if axis == "seed" else pools,
                        [d for d in domains if d != x] if axis == "domain" else domains,
                    )
                )
                for x in omissions
                if axis != "seed" or all(x not in members or len(members) > 1 for members in pools.values())
            }
    return dict(
        analysis_version="stratified-seed-dominance-v8",
        analysis_provenance=analysis_provenance("unimatrix.evaluation.stability.ranking_stability"),
        sampling_design=layout.kind,
        minimum_stratum_clusters=layout.minimum_clusters,
        primary_order=dominance_order(names, comparisons, enough),
        domain_interval_family=dict(
            alpha=0.05,
            comparisons=len(pairs) * len(domains),
            method=METHOD,
            separate_from_aggregate_family=False,
            joint_family_size=joint_family_size,
            metric_families=["reference_gain", "raw_metric", "left_strict_win", "right_strict_win"]
            if references is not None
            else ["raw_metric", "reserved_reference_gain", "left_strict_win", "right_strict_win"],
        ),
        casewise_family=dict(
            method=CASEWISE_METHOD,
            alpha=0.05,
            joint_family_size=joint_family_size,
            scope="Independent, equally weighted seed clusters under the fixed design. "
            "An all-domain certificate means the average probability of a strict casewise "
            "win exceeds one half in every domain; it does not bound score magnitude or "
            "establish mean-score dominance. Ties are non-wins.",
        ),
        scoring_version="reference-gain-v1" if references is not None else "raw-metric-v1",
        data_kind=datasets[0]["data_kind"],
        systems=len(names),
        episodes_per_system=len(keys),
        seeds=len(seeds),
        seeds_by_population=dict(Counter(manifest["population_by_seed"][str(s)] for s in seeds)),
        bootstrap_resamples=count,
        inferential=enough,
        exact_order_fraction=same_order / count,
        ranking=[
            dict(
                system=n,
                score=point[n],
                rank=point_ranks[n],
                supported_rank_range=[1 + behind[n], len(names) - ahead[n]] if enough else None,
                bootstrap_rank_interval_descriptive=[
                    percentile([r[i] for r in rank_draws[n]], p)
                    for i, p in enumerate((0.025, 0.975))
                ]
                if enough
                else None,
                top_share=top_credit[n] / count,
            )
            for n in sorted(names, key=lambda n: (-point[n], n))
        ],
        comparisons=comparisons,
        leave_one_out=sensitivity,
        interval_method=METHOD,
        bootstrap_role="descriptive_only_not_confidence_or_probability_of_superiority",
        scope="Independent seed clusters under fixed tasks, peers, models and budgets. Ties remain unresolved. "
        "Opposing domain advantages can cancel in the total; unresolved is not equivalence. "
        "Aggregate and domain intervals control one joint comparison family. "
        "Independent holdout and repeated inference are separate evidence, not inferred here.",
    )
