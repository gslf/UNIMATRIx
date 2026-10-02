"""Robust aggregates and paired comparisons for small benchmark panels (stdlib only)."""

import math
from statistics import fmean, pvariance


def iqm(values):
    """Interquartile mean: the mean of the middle half of the sorted values."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty_sample")
    lower, upper = len(ordered) / 4, 3 * len(ordered) / 4
    return sum(
        value * max(0.0, min(i + 1, upper) - max(i, lower)) for i, value in enumerate(ordered)
    ) / (upper - lower)


def optimality_gap(values, gamma=1.0):
    """How far the sample sits below the reference level; never rewards over-performance."""
    if not values:
        raise ValueError("empty_sample")
    return gamma - fmean(min(v, gamma) for v in values)


def probability_of_improvement(left, right):
    """P(left > right) over all pairs, ties counting one half (Mann-Whitney)."""
    if not left or not right:
        raise ValueError("empty_sample")
    wins = sum((a > b) + 0.5 * (a == b) for a in left for b in right)
    return wins / (len(left) * len(right))


def paired(left, right):
    """Paired difference of matched runs: mean, standard error, correlation, effective pairs."""
    if len(left) != len(right) or not left:
        raise ValueError("unmatched_samples")
    n = len(left)
    deltas = [a - b for a, b in zip(left, right)]
    mean = fmean(deltas)
    se = math.sqrt(pvariance(deltas) * n / (n - 1) / n) if n > 1 else None
    rho = None
    if n > 1 and pvariance(left) > 0 and pvariance(right) > 0:
        rho = sum((a - fmean(left)) * (b - fmean(right)) for a, b in zip(left, right)) / (
            n * math.sqrt(pvariance(left) * pvariance(right))
        )
        rho = max(-1.0, min(1.0, round(rho, 9)))
    effective = n / (1 - rho) if rho is not None and rho < 1 else None
    return dict(delta=mean, se=se, pairs=n, rho=rho, effective_pairs=effective)


def discrimination(scores):
    """Between-system spread against within-system replicate noise for one cell.

    `scores` maps a system to its replicate scores in that cell.
    """
    means = {system: fmean(values) for system, values in scores.items() if values}
    if not means:
        raise ValueError("empty_sample")
    spread = max(means.values()) - min(means.values())
    between = pvariance(list(means.values())) if len(means) > 1 else 0.0
    within_samples = [pvariance(v) for v in scores.values() if len(v) > 1]
    within = fmean(within_samples) if within_samples else None
    index = None
    if within is not None:
        index = between / within if within > 0 else (math.inf if between > 0 else 0.0)
    return dict(spread=spread, between=between, within=within, index=index, systems=len(means))


def required_pairs(sd_delta, minimum_effect, alpha_z=1.96, power_z=0.84):
    """Matched pairs needed to detect an effect with 80% power at 5% two-sided alpha."""
    if minimum_effect <= 0:
        raise ValueError("positive_effect_required")
    if sd_delta <= 0:
        return 1
    return math.ceil(((alpha_z + power_z) * sd_delta / minimum_effect) ** 2)


def spearman(left, right):
    """Rank correlation of two equally long samples; None when undefined."""
    if len(left) != len(right) or len(left) < 3:
        return None

    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                result[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return result

    a, b = ranks(left), ranks(right)
    if pvariance(a) == 0 or pvariance(b) == 0:
        return None
    n = len(a)
    covariance = sum((x - fmean(a)) * (y - fmean(b)) for x, y in zip(a, b)) / n
    return covariance / math.sqrt(pvariance(a) * pvariance(b))


def normalize(score, floor, ceiling, epsilon=0.05):
    """Signed reference gain: 0 at floor, 1 at anchor; never clip over-performance."""
    if not all(math.isfinite(x) for x in (score, floor, ceiling, epsilon)) or epsilon <= 0:
        raise ValueError("invalid_reference")
    if ceiling - floor < epsilon:
        return None
    return (score - floor) / (ceiling - floor)
