"""Non-compensatory ordering of fixed domain profiles."""


def dominance_order(names, comparisons, inferential):
    """Use simultaneous domain intervals, never aggregate scores or weights.

    Front membership is relative to the submitted cohort. Incomparability is not
    equivalence. Bootstrap limits and the fixed within-domain design still apply.
    """
    names = sorted(names)
    edges = []
    relations = []
    for pair in comparisons:
        intervals = [d["simultaneous_ci95"] for d in pair["domain_comparisons"]]
        relation = "insufficient_seeds" if not inferential else "unresolved"
        winner = loser = None
        if inferential and all(c is not None for c in intervals):
            if all(c[0] >= 0 for c in intervals) and any(c[0] > 0 for c in intervals):
                relation, winner, loser = "left_dominates", pair["left"], pair["right"]
            elif all(c[1] <= 0 for c in intervals) and any(c[1] < 0 for c in intervals):
                relation, winner, loser = "right_dominates", pair["right"], pair["left"]
            elif any(c[0] > 0 for c in intervals) and any(c[1] < 0 for c in intervals):
                relation = "incomparable_tradeoff"
        if winner is not None:
            edges.append((winner, loser))
        relations.append(dict(left=pair["left"], right=pair["right"], relation=relation))
    remaining = set(names)
    fronts = []
    while remaining:
        front = sorted(n for n in remaining if not any(b == n and a in remaining for a, b in edges))
        if not front:
            raise ValueError("Inconsistent dominance cycle")
        fronts.append(front)
        remaining.difference_update(front)
    return dict(
        method="simultaneous-domain-dominance-v1",
        fronts=fronts if inferential else [],
        relations=relations,
        ranking=[
            dict(
                system=n,
                front=i + 1 if inferential else None,
                dominated_by=sorted(a for a, b in edges if b == n),
                dominates=sorted(b for a, b in edges if a == n),
            )
            for i, front in enumerate(fronts)
            for n in front
        ],
        scope="No compensation or weights between domains. A front is a non-dominated set, "
        "not a tie or a total rank. Statistical evidence is conditional on sampled seeds, "
        "fixed domain definitions and their within-domain metric/condition weights.",
    )
