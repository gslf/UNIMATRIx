"""Upper bounds use explicit prices; no provider prices are assumed."""


def estimate(
    calls,
    input_price_per_million,
    output_price_per_million,
    max_input_tokens,
    max_output_tokens,
    attempts=3,
):
    values = [
        calls,
        input_price_per_million,
        output_price_per_million,
        max_input_tokens,
        max_output_tokens,
        attempts,
    ]
    if any(v < 0 for v in values) or attempts < 1:
        raise ValueError("nonnegative_budget_required")
    return dict(
        primary_calls=calls,
        attempts_max=calls * attempts,
        cost_upper=calls
        * attempts
        * (
            max_input_tokens * input_price_per_million
            + max_output_tokens * output_price_per_million
        )
        / 1_000_000,
    )
