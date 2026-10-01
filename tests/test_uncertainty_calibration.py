from itertools import product
from math import comb
from random import Random

import pytest

from tests.test_ranking_stability import panel
from unimatrix.evaluation.stability import ranking_stability
from unimatrix.evaluation.uncertainty import required_seeds, seed_interval, weighted_interval


@pytest.mark.parametrize("n", [4, 8, 16, 64])
@pytest.mark.parametrize("p", [0.1, 0.5, 0.9])
def test_exact_binomial_coverage(n, p):
    error = 0
    for k in range(n + 1):
        values = {i: float(i < k) for i in range(n)}
        low, high = weighted_interval(values, {i: 1 / n for i in values}, (0, 1))
        if not low <= p <= high:
            error += comb(n, k) * p**k * (1 - p) ** (n - k)
    assert error <= 0.05


def test_all_sixteen_four_seed_null_panels_have_no_false_order():
    spec, _, data = panel(4)
    spec["bootstrap"]["resamples"] = 100
    false_orders = 0
    for signs in product((-1, 1), repeat=4):
        a = data("a", lambda c: 0.5 + 0.1 * signs[c["seed"]])
        b = data("b", lambda c: 0.5)
        report = ranking_stability([a, b], spec)
        false_orders += report["comparisons"][0]["verdict"] != "unresolved"
    assert false_orders == 0


def test_roles_repeats_and_domains_do_not_create_independent_units():
    small = {("D1", "standard", s, "role", 0): 0.5 for s in range(4)}
    large = {
        (d, "standard", s, r, n): 0.5
        for d in ("D1", "D2")
        for s in range(4)
        for r in ("role", "other")
        for n in range(4)
    }
    assert seed_interval(small) == pytest.approx(seed_interval(large))


def test_preregistered_power_bound_and_direction():
    n = required_seeds(0.4, width=2, comparisons=8)
    rng = Random(292)
    successes = 0
    for _ in range(1000):

        sample = {i: 1.0 if rng.random() < 0.7 else -1.0 for i in range(n)}
        ci = weighted_interval(sample, {i: 1 / n for i in sample}, (-1, 1), comparisons=8)
        successes += ci[0] > 0
    assert successes / 1000 >= 0.8


def test_references_must_not_shrink_ranges_based_on_observed_gaps():
    sample = {("D1", "standard", s, "r", 0): 0.5 for s in range(8)}
    broad = seed_interval(sample, references={k: (0, 1) for k in sample})
    narrow = seed_interval(sample, references={k: (0.2, 0.4) for k in sample})
    assert broad == narrow
