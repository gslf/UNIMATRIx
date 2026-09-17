import json
from copy import deepcopy
from pathlib import Path

import pytest

from unimatrix.evaluation.release import audit
from unimatrix.evaluation.scoring import compare, summarize, validate

BUNDLE = Path(__file__).parents[1] / "tests/fixtures/blueprint"


def fixture():
    return json.loads((BUNDLE / "examples/synthetic-results.json").read_text()), json.loads(
        (BUNDLE / "examples/benchmark-core.json").read_text()
    )


def test_reference_matches_supplied_golden_report():
    data, manifest = fixture()
    expected = json.loads((BUNDLE / "examples/synthetic-report.json").read_text())
    result = summarize(data, manifest)
    assert result["usi"] == expected["usi"] == 69.35
    assert result["ci95"] == expected["ci95"]
    assert not result["certified"]


def test_paired_identity_and_ordering():
    data, manifest = fixture()
    result = compare(data, data, manifest)
    assert result["paired_delta"] == 0 and result["paired_ci95"] == [0, 0]
    shuffled = deepcopy(data)
    shuffled["runs"].reverse()
    assert summarize(shuffled, manifest) == summarize(data, manifest)


@pytest.mark.parametrize(
    "failure", ["missing", "duplicate", "wrong_pool", "nan", "incomplete", "hash"]
)
def test_invalid_suite_rejected(failure):
    data, manifest = fixture()
    if failure == "missing":
        data["runs"].pop()
    elif failure == "duplicate":
        data["runs"].append(data["runs"][0])
    elif failure == "wrong_pool":
        data["runs"][0]["population"] = "wrong"
    elif failure == "nan":
        data["runs"][0]["scores"][next(iter(data["runs"][0]["scores"]))] = float("nan")
    elif failure == "incomplete":
        data["runs"][0]["status"] = "infra_failed"
    else:
        data["suite_hash"] = "wrong"
    with pytest.raises(ValueError):
        validate(data, manifest)


def test_release_never_inferred_from_absent_evidence():
    result = audit({})
    assert result["missing"] and not result["certified"] and not result["ready_for_review"]


def test_export_comparison_recomputes_forged_summary_and_rejects_missing_rows():
    from unimatrix.evaluation.reports import compare_exports

    data, spec = fixture()
    exported = dict(results=data, suite=spec, report=dict(usi=999))
    result = compare_exports(exported, exported)
    assert result["left"]["usi"] == 69.35
    assert result["comparison"]["paired_ci95"] == [0, 0]
    incomplete = deepcopy(exported)
    incomplete["results"]["runs"].pop()
    with pytest.raises(ValueError, match="Missing"):
        compare_exports(exported, incomplete)
