"""Known-answer checks for the robust aggregates and paired statistics."""

import math

import pytest

from unimatrix.evaluation.stats import (
    discrimination,
    iqm,
    normalize,
    optimality_gap,
    paired,
    probability_of_improvement,
    required_pairs,
)


def test_iqm_and_optimality_gap():
    assert iqm([8, 1, 7, 2, 6, 3, 5, 4]) == 4.5
    assert iqm([0.1]) == 0.1

    assert iqm([0, 1, 2, 3, 100]) == 2
    assert iqm([0, 0, 3]) == pytest.approx(0.5)
    assert optimality_gap([0.5, 1.0, 1.5]) == pytest.approx(1 - (0.5 + 1 + 1) / 3)
    with pytest.raises(ValueError):
        iqm([])


def test_probability_of_improvement_handles_ties():
    assert probability_of_improvement([1, 2], [0, 0]) == 1
    assert probability_of_improvement([1, 1], [1, 1]) == 0.5
    assert probability_of_improvement([0], [1]) == 0


def test_paired_reports_correlation_and_effective_pairs():
    left = [0.2, 0.4, 0.6, 0.8]
    right = [0.1, 0.3, 0.5, 0.7]
    result = paired(left, right)
    assert result["delta"] == pytest.approx(0.1)
    assert result["se"] == pytest.approx(0)
    assert result["rho"] == pytest.approx(1)
    assert result["effective_pairs"] is None
    noisy = paired([0.2, 0.9, 0.1, 0.8], [0.3, 0.2, 0.7, 0.1])
    assert noisy["pairs"] == 4 and noisy["se"] > 0
    assert paired([0.5], [0.4])["se"] is None
    with pytest.raises(ValueError):
        paired([1], [1, 2])


def test_discrimination_and_normalization():
    row = discrimination({"a": [0.2, 0.2], "b": [0.8, 0.8]})
    assert row["spread"] == pytest.approx(0.6) and row["index"] == math.inf
    single = discrimination({"a": [0.2], "b": [0.5]})
    assert single["within"] is None and single["index"] is None and single["systems"] == 2
    assert normalize(0.5, 0.2, 0.8) == pytest.approx(0.5)
    assert normalize(0.9, 0.2, 0.8) == pytest.approx(7 / 6)
    assert normalize(0.1, 0.2, 0.8) == 0
    assert normalize(0.5, 0.48, 0.5) is None
    assert required_pairs(0.1, 0.05) == 32 and required_pairs(0, 0.05) == 1
