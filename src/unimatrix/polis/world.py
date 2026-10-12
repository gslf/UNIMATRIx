"""The Polis world: state, weekly clock and orchestration.

A week runs in two halves. `begin_week` applies the scheduled and conditional events of
the coming week (story graph, rumours, merchants, probes) so the steward's briefing reflects
them. `step` applies the steward's decision, lets every resident live its week, updates the
environment, the score ledger and the collapse rules, and records a frame for playback.
"""

import copy
from statistics import fmean

from . import ENGINE, npc, story
from .actions import apply_decision
from .briefing import briefing
from .catalog import SEASONS
from .metrics import clamp, snapshot
from .rng import draw
from .scoring import Ledger


def new_resident(spec, week, conventions):
    state = spec["state"]
    return dict(
        id=spec["id"], name=spec["name"], age=spec["age"], role=spec["role"], faction=spec["faction"],
        archetype=spec["archetype"], bio=spec["bio"], newcomer=spec["newcomer"], flags=list(spec["flags"]),
        secrets=list(spec["secrets"]), traits=dict(spec["traits"]), skills=dict(spec["skills"]),
        decline=copy.deepcopy(spec["decline"]),
        coins=float(state["coins"]), food=float(state["food"]), health=float(state["health"]),
        stress=float(state["stress"]), loneliness=float(state["loneliness"]),
        grievance=float(state["grievance"]), trust=float(state["trust"]), trust_cap=1.0,
        legit=0.5, wellbeing=6.0, medicine=0, tools=0,
        concern=(dict(spec["concern"], since=week) if spec["concern"] else None),
        status="present", joined=week, ended=None,
        task="work", activity="work", declined=False, sick=False, sick_since=None, hungry=False,
        hungry_weeks=0, low_weeks=0, withdrawn=False, distress_weeks=0, protesting=False,
        beliefs={}, sources={}, learning={}, convention=dict(conventions), last_contact=None,
        talks=[], consulted={}, honoured=False, prejudice=0.0, perceived_take=4.0, last_take=0.0,
        violations=0, contact=0.0, support=0.0, harm=0.0, ignored=0, broken=0, grief=0.0,
        sanctioned=0.0, busy_weeks=0, last_message=-10, knows={}, fake_medicine=0, stolen=0.0,
        deterrence=0.0, adopted={}, fear=0.0, meetings=0, prod_penalty=0.0, griev_effect=0.0, hoard=False,
        caution=0.0, teaching=False, violation=0.0, income=0.0, prepared=[], acknowledged=0.0,
        steward_helped=-99,
    )


class World:
    def __init__(self, recipe, seed):
        self.recipe = recipe
        self.seed = seed
        self.engine = ENGINE
        self.horizon = recipe["ticks"]
        self.week = 1
        self.begun = False
        world = recipe["world"]
        self.stores = dict(treasury=float(world["treasury"]), granary=float(world["granary"]),
                           medicine=float(world["medicine"]), tools=float(world["tools"]), fake_medicine=0.0)
        self.policies = dict(world["policies"])
        self.policy_origin = {}
        self.conventions = dict(official=dict(world["conventions"]), required=dict(world["conventions"]),
                                shifts=[])
        self.residents = {}
        for spec in recipe["residents"]:
            self.residents[spec["id"]] = new_resident(spec, 1, self.conventions["official"])
        self.initial_population = len(self.residents)
        self.ties = {rid: {} for rid in self.residents}
        for a, b, strength in recipe["ties"]:
            self.set_tie(a, b, strength)
        self.factions = {f["id"]: f for f in recipe["factions"]}
        commons = world["commons"]
        self.commons = dict(name=commons["name"], stock=float(commons["stock"]),
                            capacity=float(commons["capacity"]), growth=commons["growth"],
                            critical=commons["critical"], floor=commons["efficiency_floor"],
                            collapsed=False, last_harvest=0.0, last_efficiency=1.0, survey=None,
                            initial=float(commons["stock"]))
        self.projects = {key: dict(spec, id=key, status="available", progress={k: 0.0 for k in spec["needs"]},
                                   started=None, done=None, workers=[])
                         for key, spec in recipe["projects"].items()}
        self.merchants = {}
        for spec in recipe["merchants"]:
            merchant = copy.deepcopy(spec)
            merchant.update(present=False, arrived=None, leaves=None, stock={}, prices={}, batches={},
                            counters={}, price_factor=1.0, visit=0)
            self.merchants[spec["id"]] = merchant
        self.market_pool = 0.0
        self.monitor_chance = 0.0
        self.market_sellers = {}
        self.facts = {f["id"]: dict(f, status="dormant", announcements=[], evidence=[]) for f in recipe["facts"]}
        self.probes = {}
        self.conflicts = {}
        self.promises = []
        self.requests = []
        self.proposals = []
        self.votes = []
        self.decrees = []
        self.modifiers = []
        self.epidemics = []
        self.manuals = {}
        self.documenting = {}
        self.counters = {}
        self.inbox = []
        self.results = []
        self.chronicle = []
        self.note = ""
        self.gatherings = {}
        self.story = story.Story(self)
        self.ledger = Ledger(self)
        self.collapsed = None
        self.outcomes = []
        self.streaks = dict(revolt=0, legitimacy=0, famine=0)
        self.frames = []
        self.decisions = []
        self.order = sorted(self.residents, key=lambda rid: (draw(seed, "briefing-order", rid), rid))
        self.order_seed = seed
        for r in self.residents.values():
            npc.refresh(self, r)
        self.ledger.start()
        self.frames.append(self.frame(initial=True))

    # ----- helpers -----
    def present(self):
        return [self.residents[rid] for rid in self.residents if self.residents[rid]["status"] == "present"]

    def resident(self, rid):
        r = self.residents.get(rid)
        return r if r is not None and r["status"] == "present" else None

    def tie(self, a, b):
        return self.ties.get(a, {}).get(b, 0.0)

    def set_tie(self, a, b, strength):
        strength = clamp(strength)
        self.ties.setdefault(a, {})
        self.ties.setdefault(b, {})
        if strength <= 0.001:
            self.ties[a].pop(b, None)
            self.ties[b].pop(a, None)
        else:
            self.ties[a][b] = strength
            self.ties[b][a] = strength

    @property
    def season(self):
        start = SEASONS.index(self.recipe["world"]["start_season"])
        length = self.recipe["world"]["season_length"]
        return SEASONS[(start + (self.week - 1) // length) % 4]

    def modifier(self, key):
        factor = 1.0
        for item in self.modifiers:
            if item["key"] == key and item["until"] >= self.week:
                factor *= item["factor"]
        return factor

    def shock_active(self, name):
        return any(m.get("shock") == name and m["until"] >= self.week for m in self.modifiers)

    def benefit(self, key, default=None):
        values = [p["benefit"][key] for p in self.projects.values() if p["status"] == "done" and key in p["benefit"]]
        if not values:
            return default
        if key in {"spoilage"}:
            return min(values)
        result = values[0]
        for value in values[1:]:
            result = result * value if key in {"fish_growth", "sale_price", "convention_speed", "learning", "farm"} \
                else result + value
        return result

    def say(self, text):
        self.chronicle.append(dict(week=self.week, text=text))

    def message(self, sender, text, kind="message", about=None, request=None):
        self.inbox.append(dict(week=self.week, sender=sender, text=text, kind=kind, about=about,
                               request=request))

    def count(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1

    def snapshot(self):
        return snapshot(self)

    @property
    def done(self):
        return self.collapsed is not None or self.week > self.horizon

    # ----- weekly clock -----
    def begin_week(self):
        if self.begun or self.done:
            return
        self.begun = True
        self.inbox = [m for m in self.inbox if m["week"] >= self.week - 1]
        npc.merchants_schedule(self)
        npc.facts_schedule(self)
        self.story.advance()
        npc.probes_schedule(self)
        if self.collapsed is not None:
            self.ledger.close()
            self.frames.append(self.frame())

    def observe(self):
        self.begin_week()
        if self.done:
            return None
        return briefing(self)

    def step(self, decision, raw=None, error=None):
        self.begin_week()
        if self.done:
            return
        self.results = []
        if error:
            self.results.append(dict(action="(response)", verb="parse", status="rejected",
                                     text=f"Your answer could not be read as one JSON object ({error}); "
                                          "no actions were taken."))
        for r in self.present():
            r["support"] = 0.0
            r["contact"] = 0.0
            r["sanctioned"] = 0.0
        applied = apply_decision(self, decision)
        forecasts = {k: p["answer"] for k, p in self.probes.items() if p["answered"] == self.week}
        self.decisions.append(dict(week=self.week, applied=applied, raw=raw, note=self.note, forecasts=forecasts,
                                   error=error))
        npc.live_week(self)
        self.ledger.week()
        self.check_collapse()
        final = self.collapsed is None and self.week == self.horizon
        if final:
            self.story.finish()
        if self.collapsed is not None or final:
            self.ledger.close()
        self.frames.append(self.frame())
        self.week += 1
        self.begun = False

    def check_collapse(self):
        rules = self.recipe["collapse"]
        data = self.snapshot()
        reasons = []
        if rules["population"] is not None and data["population_share"] < rules["population"]:
            reasons.append(("depopulation", "Depopulation: too many residents died or left"))
        if rules["commons"] and self.commons["collapsed"]:
            reasons.append(("commons", f"Ecological collapse: {self.commons['name']} fell below its critical stock"))
        if rules["revolt_share"] is not None:
            self.streaks["revolt"] = self.streaks["revolt"] + 1 if data["protest"] >= rules["revolt_share"] else 0
            if self.streaks["revolt"] >= rules["revolt_weeks"]:
                reasons.append(("revolt", "Revolt: the residents rose against the steward"))
        if rules["legitimacy"] is not None:
            low = data["legitimacy"] < rules["legitimacy"]
            self.streaks["legitimacy"] = self.streaks["legitimacy"] + 1 if low else 0
            if self.streaks["legitimacy"] >= rules["legitimacy_weeks"]:
                reasons.append(("legitimacy", "No confidence: the council removed the steward"))
        if rules["famine_share"] is not None:
            starving = data["starving"] >= rules["famine_share"]
            self.streaks["famine"] = self.streaks["famine"] + 1 if starving else 0
            if self.streaks["famine"] >= rules["famine_weeks"]:
                reasons.append(("famine", "Famine: hunger spread through the community"))
        if rules["fragmentation"] is not None and self.week >= 4 and data["population"] >= 4 \
                and data["cohesion"] < rules["fragmentation"]:
            reasons.append(("fragmentation", "Fragmentation: the community split into isolated groups"))
        if reasons:
            cause, label = reasons[0]
            self.collapse(cause, label)

    def collapse(self, cause, label):
        if self.collapsed is None:
            self.collapsed = dict(week=self.week, cause=cause, label=label)
            self.say("COLLAPSE — " + label + ".")

    # ----- records -----
    def frame(self, initial=False):
        data = self.snapshot()
        residents = []
        for rid in self.residents:
            r = self.residents[rid]
            residents.append(dict(
                id=rid, status=r["status"], activity=r["activity"] if r["status"] == "present" else r["status"],
                wb=round(r["wellbeing"], 2), st=round(r["stress"], 3), lo=round(r["loneliness"], 3),
                gr=round(r["grievance"], 3), tr=round(r["trust"], 3), lg=round(r["legit"], 3),
                hp=round(r["health"], 1), coins=round(r["coins"], 1), food=round(r["food"], 1),
                protest=r["protesting"], sick=r["sick"], hungry=r["hungry"],
                concern=(dict(kind=r["concern"]["kind"], intensity=round(r["concern"]["intensity"], 2))
                         if r["concern"] else None),
                task=r["task"], skills=dict(r["skills"])))
        ties = [[a, b, round(s, 3)] for a in self.ties for b, s in self.ties[a].items() if a < b and s >= 0.05]
        return dict(
            week=0 if initial else self.week,
            season=self.season,
            metrics={k: (round(v, 4) if isinstance(v, float) else v) for k, v in data.items()
                     if k not in {"skills", "beliefs", "conventions"}},
            skills=data["skills"], beliefs={k: {kk: round(vv, 3) for kk, vv in v.items()}
                                            for k, v in data["beliefs"].items()},
            conventions={k: round(v, 3) for k, v in data["conventions"].items()},
            residents=residents,
            ties=ties,
            stores={k: round(v, 1) for k, v in self.stores.items()},
            commons=dict(stock=round(self.commons["stock"], 1), harvest=round(self.commons["last_harvest"], 2)),
            projects={k: p["status"] for k, p in self.projects.items()},
            conflicts={k: dict(a=c["a"], b=c["b"], intensity=round(c["intensity"], 2), resolved=c["resolved"])
                       for k, c in self.conflicts.items()},
            story=self.story.status(),
            events=[c["text"] for c in self.chronicle if c["week"] == self.week] if not initial else [],
            actions=(self.decisions[-1]["applied"] if self.decisions and not initial else []),
            steward=(dict(note=self.decisions[-1]["note"][:600], forecasts=self.decisions[-1]["forecasts"],
                          error=self.decisions[-1]["error"])
                     if self.decisions and not initial else None),
            points=self.ledger.running(),
            collapsed=self.collapsed,
        )

    def mean(self, key):
        present = self.present()
        return fmean(r[key] for r in present) if present else 0.0
