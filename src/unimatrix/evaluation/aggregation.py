"""Explain the assumptions and cancellation in an equal-domain arithmetic mean."""

from math import fsum


def aggregation_audit(differences):
    """Exact sensitivity of observed means, not an additional significance test.

    Total variation is the share of total domain weight transferred. Find the
    smallest transfer from uniform weights that reaches a tie by moving weight
    from the largest signed difference to the smallest (a linear programme).
    """
    if not differences:
        raise ValueError("Domain differences required")
    domains = sorted(differences)
    count = len(domains)
    delta = fsum(differences.values()) / count
    gross = fsum(abs(v) for v in differences.values()) / count
    weights = dict.fromkeys(domains, 1 / count)
    transfer = 0.0
    direction = 1 if delta >= 0 else -1
    ordered = sorted(domains, key=lambda d: direction * differences[d])
    receiver = ordered[0]
    remaining = abs(delta)
    reachable = delta == 0 or direction * differences[receiver] <= 0
    if reachable and remaining:
        for donor in reversed(ordered[1:]):
            gap = direction * (differences[donor] - differences[receiver])
            if gap <= 0:
                continue
            amount = min(weights[donor], remaining / gap)
            weights[donor] -= amount
            weights[receiver] += amount
            transfer += amount
            remaining = max(0.0, remaining - amount * gap)
            if remaining <= 1e-14:
                break
    return dict(
        method="equal-domain-arithmetic-mean-v1",
        mean_difference=delta,
        mean_absolute_domain_difference=gross,
        cancelled_fraction=1 - abs(delta) / gross if gross else 0.0,
        observed_opposing_advantages=min(differences.values()) < 0 < max(differences.values()),
        minimum_weight_transfer_to_tie=transfer if reachable else None,
        tie_weights=weights if reachable else None,
        contributions=[
            dict(
                domain=d,
                weight=1 / count,
                difference=differences[d],
                contribution=differences[d] / count,
            )
            for d in domains
        ],
        scope="Observed domain means only. Weight transfer is a preference sensitivity, "
        "not a probability or a confidence interval. Equal weights allow complete "
        "compensation between domains; they do not establish a single latent ability.",
    )
