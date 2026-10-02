"""Preregistered scenario clocks; historical 240-tick cases retain their timing."""

HORIZONS = (72, 240)


def scaled(ticks, original):
    return max(1, original * ticks // 240)


def span(state, original):
    return scaled(state.scenario["horizon"], original)
