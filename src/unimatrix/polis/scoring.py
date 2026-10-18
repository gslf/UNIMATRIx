"""Unbounded scoring in natural units: every domain counts something real.

Points accumulate week by week (resident-weeks of trust, coins of net worth created,
fish harvested, service-weeks of public works ...). Recipe exchange rates convert each
domain's units into points. Nothing caps the total: a better steward can always create
more. A collapse stops the run and deducts 10% of the accumulated points.
"""

import math
from statistics import fmean

from .catalog import DOMAINS
from .metrics import gini, integrated, networth, successor_depth

SCORING_RULE = "accumulated-collapse-10pct-v2"
COLLAPSE_PENALTY_RATE = 0.10


def evaluate_score(score):
    """Apply the scoring rule to a recorded ledger without replaying the simulation."""
    earned = round(sum(domain["points"] for domain in score["domains"].values()), 3)
    penalty = round(earned * COLLAPSE_PENALTY_RATE, 3) if score.get("collapsed") else 0.0
    return dict(score, total=round(earned - penalty, 3), earned=earned,
                penalty=penalty, penalty_rate=COLLAPSE_PENALTY_RATE,
                rule=SCORING_RULE)


class Ledger:
    def __init__(self, world):
        self.world = world
        self.units = {d: 0.0 for d in DOMAINS}
        self.bonus = {d: 0.0 for d in DOMAINS}
        self.harvest = 0.0
        self.apprentice_levels = 0
        self.concerns_relieved = 0
        self.promises_kept = 0
        self.promises_broken = 0
        self.conflicts_resolved = 0
        self.violence = 0
        self.deaths = []
        self.departures = []
        self.gifts = []
        self.forecasts = []
        self.wellby = 0.0
        self.wellby_equity = 0.0
        self.history = []
        self.closed = False
        self.final = None

    def start(self):
        world = self.world
        self.start_networth = networth(world)
        self.start_stock = world.commons["stock"]

    def week(self):
        world = self.world
        present = world.present()
        n = len(present)
        informed = 0.0
        for fact in world.facts.values():
            if fact["status"] != "live":
                continue
            for r in present:
                p = r["beliefs"].get(fact["id"])
                if p is None:
                    continue
                if (p >= 0.7 and fact["truth"]) or (p <= 0.3 and not fact["truth"]):
                    informed += 1
                elif (p >= 0.7 and not fact["truth"]) or (p <= 0.3 and fact["truth"]):
                    informed -= 1
        self.units["D1"] += informed
        self.units["D3"] += sum(p["value"] for p in world.projects.values() if p["status"] == "done")
        self.units["D5"] += sum(r["trust"] for r in present)
        legitimacy = fmean(r["legit"] for r in present) if present else 0.0
        inequality = gini([r["coins"] for r in present])
        self.units["D6"] += n * legitimacy * (1 - inequality)
        self.units["D7"] += sum(successor_depth(world).values())
        adapted = 0.0
        for shift in world.conventions["shifts"]:
            if shift["week"] <= world.week:
                required = world.conventions["required"][shift["domain"]]
                adapted += sum(1 for r in present if r["convention"].get(shift["domain"]) == required)
        newcomers = [r for r in present if r["newcomer"]]
        adapted += sum(1 for r in newcomers if integrated(world, r))
        self.units["D8"] += adapted
        wellbeing = [max(0.0, r["wellbeing"] - 2) for r in present]
        self.wellby += sum(wellbeing) / 52
        self.wellby_equity += sum(wellbeing) * (1 - gini(wellbeing)) / 52
        self.history.append(dict(week=world.week, **{k: round(v, 3) for k, v in self.running().items()}))

    def resolve_probes(self, fact):
        rate = self.world.recipe["scoring"]["forecast_bit"]
        for probe in self.world.probes.values():
            if probe["fact"] != fact["id"] or probe["points"] is not None:
                continue
            p = probe["answer"]
            if p is None:
                probe["points"] = 0.0
            else:
                likelihood = p if fact["truth"] else 1 - p
                probe["points"] = max(-2.0, 1 + math.log2(max(likelihood, 1e-6))) * rate
            self.forecasts.append(dict(probe=probe["id"], answer=p, truth=fact["truth"], points=probe["points"]))

    def reward(self, rewards):
        for domain, value in rewards.items():
            self.bonus[domain] += value

    def events(self):
        s = self.world.recipe["scoring"]
        return dict(
            D1=sum(f["points"] for f in self.forecasts),
            D5=(s["promise_kept"] * self.promises_kept + s["promise_broken"] * self.promises_broken
                + s["conflict_resolved"] * self.conflicts_resolved + s["violence"] * self.violence
                + s["concern_relieved"] * self.concerns_relieved),
            D7=s["apprentice_level"] * self.apprentice_levels,
        )

    def raw(self):
        world = self.world
        units = dict(self.units)
        for domain, value in self.events().items():
            units[domain] += value
        units["D2"] = networth(world) - self.start_networth
        units["D4"] = self.harvest + (world.commons["stock"] - self.start_stock)
        for domain in units:
            units[domain] += self.bonus[domain]
        return units

    def running(self):
        rates = self.world.recipe["scoring"]["rates"]
        return {d: max(0.0, v) * rates[d] for d, v in self.raw().items()}

    def close(self):
        if self.closed:
            return
        self.closed = True
        world = self.world
        raw = self.raw()
        rates = world.recipe["scoring"]["rates"]
        domains = {}
        for d in DOMAINS:
            value = max(0.0, raw[d])
            domains[d] = dict(name=DOMAINS[d]["name"], unit=DOMAINS[d]["unit"], units=round(raw[d], 3),
                              rate=rates[d], points=round(value * rates[d], 3))
        collapsed = world.collapsed
        self.final = evaluate_score(dict(
            collapsed=collapsed,
            survived_weeks=(collapsed["week"] - 1) if collapsed else world.horizon,
            domains=domains,
            wellby=round(self.wellby, 3),
            wellby_equity=round(self.wellby_equity, 3),
            events=dict(promises_kept=self.promises_kept, promises_broken=self.promises_broken,
                        conflicts_resolved=self.conflicts_resolved, violence=self.violence,
                        concerns_relieved=self.concerns_relieved, apprentice_levels=self.apprentice_levels,
                        deaths=len(self.deaths), departures=len(self.departures), harvest=round(self.harvest, 2)),
            forecasts=self.forecasts,
        ))
        return self.final
