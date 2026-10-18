"""Run a society with a reference steward, in-process and in milliseconds."""

from .baselines import REFERENCES, make
from .recipe import normalize
from .report import build_report
from .world import World


def run(recipe, seed, policy="expert"):
    """`recipe` must already be normalized."""
    world = World(recipe, seed)
    steward = make(policy, world, seed) if isinstance(policy, str) else policy
    while not world.done:
        obs = world.observe()
        if obs is None:
            break
        world.step(steward.decide(obs))
    return world


def simulate(recipe, seed=None, policy="expert", trace=True):
    recipe = normalize(recipe)
    seed = recipe["seeds"][0] if seed is None else seed
    world = run(recipe, seed, policy)
    report = build_report(world)
    if trace:
        report["frames"] = world.frames
    return report


def calibrate(recipe, seeds=None, policies=REFERENCES):
    """Score every reference steward on every seed: the difficulty profile of a recipe."""
    recipe = normalize(recipe)
    seeds = seeds or recipe["seeds"]
    rows = []
    for policy in policies:
        for seed in seeds:
            world = run(recipe, seed, policy)
            final = world.ledger.final
            rows.append(dict(policy=policy, seed=seed, total=final["total"], earned=final["earned"],
                             collapsed=final["collapsed"], survived_weeks=final["survived_weeks"],
                             domains={d: v["points"] for d, v in final["domains"].items()},
                             units={d: v["units"] for d, v in final["domains"].items()},
                             wellby=final["wellby"], path=[s["target"] for s in world.story.path]))
    return rows


def suggested_rates(rows, recipe, target=1000.0):
    """Exchange rates that would give the expert reference `target` points per domain."""
    expert = [r for r in rows if r["policy"] == "expert"]
    rates = {}
    for domain in recipe["scoring"]["rates"]:
        values = [max(0.0, r["units"][domain]) for r in expert]
        mean = sum(values) / len(values) if values else 0.0
        rates[domain] = round(target / mean, 4) if mean > 0 else recipe["scoring"]["rates"][domain]
    # A reference that builds little must not give construction a huge exchange rate.
    # This is an opportunity-based rate, not a cap on accumulated score.
    service = recipe["ticks"] * sum(p["value"] for p in recipe["projects"].values())
    bonuses = sum(max(0, (n.get("outcome") or {}).get("reward", {}).get("D3", 0))
                  for n in recipe["story"]["nodes"])
    if service + bonuses > 0:
        rates["D3"] = min(rates["D3"], round(target / (service + bonuses), 4))
    return rates
