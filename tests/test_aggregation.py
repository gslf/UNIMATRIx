import pytest

from unimatrix.evaluation.aggregation import aggregation_audit


def test_cancellation_is_not_similarity():
    result = aggregation_audit({"trade": 0.1, "relationships": -0.1, "other": 0})
    assert result["mean_difference"] == 0
    assert result["cancelled_fraction"] == 1
    assert result["observed_opposing_advantages"]
    same = aggregation_audit({"trade": 0, "relationships": 0})
    assert same["cancelled_fraction"] == 0
    assert not same["observed_opposing_advantages"]


def test_exact_minimum_weight_transfer_and_direction_symmetry():
    differences = {"a": 0.12, "b": -0.10, "c": 0, "d": 0}
    result = aggregation_audit(differences)
    assert result["minimum_weight_transfer_to_tie"] == pytest.approx(0.005 / 0.22)
    weights = result["tie_weights"]
    assert sum(weights.values()) == pytest.approx(1)
    assert all(0 <= w <= 1 for w in weights.values())
    assert sum(weights[d] * differences[d] for d in weights) == pytest.approx(0)
    reversed_result = aggregation_audit({k: -v for k, v in differences.items()})
    assert reversed_result["tie_weights"] == pytest.approx(weights)
    assert reversed_result["cancelled_fraction"] == pytest.approx(result["cancelled_fraction"])


def test_multiple_donors_and_unreachable_tie():
    result = aggregation_audit({"a": 4, "b": 3, "c": -1})
    assert result["minimum_weight_transfer_to_tie"] == pytest.approx(5 / 12)
    assert result["tie_weights"] == pytest.approx({"a": 0, "b": 0.25, "c": 0.75})
    assert aggregation_audit({"a": 1, "b": 2})["tie_weights"] is None
    assert aggregation_audit({"a": 1, "b": 0})["minimum_weight_transfer_to_tie"] == 0.5
