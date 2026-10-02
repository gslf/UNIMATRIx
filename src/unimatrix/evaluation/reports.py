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
    if left.get("references") != right.get("references"):
        raise ValueError("Cannot compare mismatched reference anchors")
    refs = {tuple(r["case"]): (r["floor"], r["anchor"]) for r in left.get("references", [])} or None
    difference = compare(left["results"], right["results"], spec)
    if refs is not None:
        from .stability import ranking_stability


        a, b = (
            dict(left["results"], candidate_id="left"),
            dict(right["results"], candidate_id="right"),
        )
        difference["reference_gain"] = ranking_stability([a, b], spec, refs)["comparisons"][0]
    return dict(
        left=summarize(left["results"], spec, refs),
        right=summarize(right["results"], spec, refs),
        comparison=difference,
    )
