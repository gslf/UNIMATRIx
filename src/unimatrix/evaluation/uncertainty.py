"""Finite-sample Hoeffding bounds over independent seed clusters.

All observations sharing a seed form ONE unit, across domains, roles and repeats.
Weights implement exactly the reported equal-domain/equal-condition estimator.
Bounds are fixed by the metric definition, never estimated from observed variance.
"""

import math
from collections import defaultdict

METHOD = "weighted-Hoeffding-seed-clusters-v1"
MIN_REFERENCE_GAP = 0.05


def score_bounds(references=None, paired=False):
    if references is None:
        return (-1.0, 1.0) if paired else (0.0, 1.0)
    for floor, anchor in references.values():
        if not (0 <= floor <= 1 and 0 <= anchor <= 1 and anchor - floor >= MIN_REFERENCE_GAP):
            raise ValueError("complete_nondegenerate_unit_interval_references_required")


    width = 1 / MIN_REFERENCE_GAP
    return (-width, width) if paired else (0.0, width)


def weighted_interval(values, weights, bounds, *, alpha=0.05, comparisons=1):
    if not values or set(values) != set(weights):
        raise ValueError("matched_nonempty_cluster_weights_required")
    low, high = bounds
    if not (
        math.isfinite(low)
        and math.isfinite(high)
        and low < high
        and 0 < alpha < 1
        and type(comparisons) is int
        and comparisons >= 1
    ):
        raise ValueError("invalid_interval_design")
    if any(not math.isfinite(v) or not low - 1e-12 <= v <= high + 1e-12 for v in values.values()):
        raise ValueError("score_outside_preregistered_bounds")
    if any(not math.isfinite(w) or w <= 0 for w in weights.values()) or not math.isclose(
        sum(weights.values()), 1
    ):
        raise ValueError("positive_normalized_weights_required")
    mean = sum(weights[k] * v for k, v in values.items())
    radius = (high - low) * math.sqrt(
        math.log(2 * comparisons / alpha) * sum(w * w for w in weights.values()) / 2
    )
    return [max(low, mean - radius), min(high, mean + radius)]


def seed_estimator(values, domains=None):
    domains = sorted(domains or {k[0] for k in values})
    cells = {
        (d, c): [k for k in sorted(values) if k[0] == d and k[1] == c]
        for d in domains
        for c in sorted({k[1] for k in values if k[0] == d})
    }
    contributions, weights = defaultdict(float), defaultdict(float)
    for (d, _), keys in cells.items():
        conditions = sum(cd == d for cd, _ in cells)
        weight = 1 / len(domains) / conditions / len(keys)
        for key in keys:
            contributions[key[2]] += weight * values[key]
            weights[key[2]] += weight
    return {s: v / weights[s] for s, v in contributions.items()}, dict(weights)


def seed_interval(values, *, references=None, paired=False, domains=None, comparisons=1):
    clusters, weights = seed_estimator(values, domains)
    return weighted_interval(
        clusters, weights, score_bounds(references, paired), comparisons=comparisons
    )


def required_seeds(effect, *, width=2, comparisons=1, alpha=0.05, power=0.8):
    """Distribution-free sufficient n for a one-direction effect; no pilot SD guess."""
    if (
        not all(math.isfinite(v) for v in (effect, width, alpha, power))
        or not 0 < effect <= width
        or not 0 < alpha < 1
        or not 0 < power < 1
        or type(comparisons) is not int
        or comparisons < 1
    ):
        raise ValueError("invalid_power_design")
    return math.ceil(
        width**2
        * (math.sqrt(math.log(2 * comparisons / alpha)) + math.sqrt(math.log(1 / (1 - power)))) ** 2
        / (2 * effect**2)
    )
