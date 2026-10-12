"""Counter-based randomness: every draw is addressed by (seed, week, entity, purpose).

Draws never depend on how many other draws happened before them, so two candidates that
take different actions still face the same exogenous luck (common random numbers).
"""

import hashlib


def _key(seed, key):
    return repr((seed,) + tuple(key)).encode()


def draw(seed, *key):
    """Uniform float in [0, 1)."""
    digest = hashlib.blake2b(_key(seed, key), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 18446744073709551616


def chance(seed, probability, *key):
    return probability > 0 and draw(seed, *key) < probability


def pick(seed, options, *key):
    options = list(options)
    if not options:
        return None
    return options[int(draw(seed, *key) * len(options))]


def normal(seed, mean, sd, *key):
    """Approximately normal (Irwin–Hall with four uniforms)."""
    total = sum(draw(seed, *key, i) for i in range(4))
    return mean + sd * (total - 2) * 1.7320508


def order(seed, items, *key):
    return sorted(items, key=lambda item: (draw(seed, *key, item), item))
