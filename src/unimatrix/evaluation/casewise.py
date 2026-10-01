"""Exact, deliberately narrow certificate for paired seed-cluster wins.

For independent Bernoulli wins with possibly different probabilities p_i,
P(all n wins) = product(p_i) <= mean(p_i) ** n by AM-GM. Thus under the
one-sided null that their equally weighted mean is at most 1/2, the all-win
event has probability at most 2 ** -n. Ties count as failures to win.
This says nothing about the size or mean of a score difference.
"""

import math

METHOD = "exact-all-wins-average-strict-win-probability-v1"


def all_win_certificate(clusters, weights, *, comparisons, alpha=0.05, eligible=True):
    """Certify only unanimous strict wins with equal fixed-design cluster weights."""
    if (
        not clusters or set(clusters) != set(weights)
        or type(comparisons) is not int or comparisons < 1
        or not 0 < alpha < 1
        or any(not math.isfinite(value) for value in clusters.values())
        or any(not math.isfinite(weight) or weight <= 0 for weight in weights.values())
        or not math.isclose(sum(weights.values()), 1)
    ):
        raise ValueError("invalid_casewise_design")
    n = len(clusters)
    wins = sum(value > 0 for value in clusters.values())
    losses = sum(value < 0 for value in clusters.values())
    ties = n - wins - losses
    equal_weights = all(math.isclose(weight, 1 / n, rel_tol=1e-12, abs_tol=1e-12)
                        for weight in weights.values())
    available = eligible and equal_weights and n >= 4
    unanimous = available and (wins == n or losses == n)
    p_upper = 2.0 ** -n if unanimous else None
    adjusted = min(1.0, p_upper * comparisons) if unanimous else None
    certified = unanimous and adjusted <= alpha
    return dict(
        clusters=n, wins=wins, losses=losses, ties=ties,
        equal_cluster_weights=equal_weights,
        verdict=("unavailable_design" if not available else
                 "left_majority" if certified and wins == n else
                 "right_majority" if certified and losses == n else "unresolved"),
        one_sided_p_upper_bound=p_upper,
        family_adjusted_p_upper_bound=adjusted,
        simultaneous_lower_bound_for_winner_strict_win_probability=(
            (alpha / comparisons) ** (1 / n) if certified else None
        ),
    )
