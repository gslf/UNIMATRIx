from copy import deepcopy

from unimatrix.evaluation.dominance import dominance_order


def pair(a, b, intervals):
    return dict(left=a, right=b, domain_comparisons=[dict(simultaneous_ci95=c) for c in intervals])


def test_no_compensation_even_with_large_aggregate_advantage():
    p = pair("a", "b", [[100, 101], [-0.02, -0.01]])
    result = dominance_order(["a", "b"], [p], True)
    assert result["fronts"] == [["a", "b"]]
    assert result["relations"][0]["relation"] == "incomparable_tradeoff"

    scaled = deepcopy(p)
    for d, factor in zip(scaled["domain_comparisons"], [0.00001, 100000]):
        d["simultaneous_ci95"] = [v * factor for v in d["simultaneous_ci95"]]
    assert dominance_order(["b", "a"], [scaled], True) == result


def test_dominance_fronts_and_uncertain_domains():
    pairs = [
        pair("a", "b", [[0.1, 0.2], [0, 0]]),
        pair("a", "c", [[0.2, 0.3], [0.1, 0.2]]),
        pair("b", "c", [[0.1, 0.2], [0.1, 0.2]]),
    ]
    r = dominance_order(["c", "a", "b"], pairs, True)
    assert r["fronts"] == [["a"], ["b"], ["c"]]
    assert r["ranking"][2]["dominated_by"] == ["a", "b"]
    uncertain = pair("a", "b", [[0.1, 0.2], [-0.01, 0.01]])
    r = dominance_order(["a", "b"], [uncertain], True)
    assert r["fronts"] == [["a", "b"]]
    assert r["relations"][0]["relation"] == "unresolved"
    assert dominance_order(["a", "b"], [uncertain], False)["fronts"] == []
