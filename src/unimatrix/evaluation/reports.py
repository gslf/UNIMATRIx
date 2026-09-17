"""Compare complete exports by recomputing scores from their normalized rows."""

from .scoring import compare, summarize


def compare_exports(left, right):
    if not all(
        isinstance(value, dict) and {"suite", "results"} <= value.keys() for value in (left, right)
    ):
        raise ValueError("Two complete bench score exports are required")
    if left["suite"] != right["suite"]:
        raise ValueError("Cannot compare mismatched suites")
    spec = left["suite"]
    difference = compare(left["results"], right["results"], spec)
    return dict(
        left=summarize(left["results"], spec),
        right=summarize(right["results"], spec),
        comparison=difference,
    )
