"""Aggregate indicators of a Polis world, used by story conditions, collapse rules,
scoring and the sociological report."""

from statistics import fmean

from .catalog import REFERENCE_PRICES, SKILLS


def clamp(value, low=0.0, high=1.0):
    return low if value < low else high if value > high else value


def gini(values):
    values = sorted(max(0.0, v) for v in values)
    n, total = len(values), sum(values)
    if n < 2 or total <= 0:
        return 0.0
    weighted = sum((i + 1) * v for i, v in enumerate(values))
    return clamp((2 * weighted) / (n * total) - (n + 1) / n)


def components(world, threshold=0.15):
    """Connected groups of present residents linked by ties of at least `threshold`."""
    people = [r["id"] for r in world.present()]
    seen, groups = set(), []
    for start in people:
        if start in seen:
            continue
        group, stack = [], [start]
        seen.add(start)
        while stack:
            current = stack.pop()
            group.append(current)
            for other, strength in world.ties.get(current, {}).items():
                if strength >= threshold and other not in seen and world.residents[other]["status"] == "present":
                    seen.add(other)
                    stack.append(other)
        groups.append(sorted(group))
    return sorted(groups, key=len, reverse=True)


def cohesion(world):
    present = world.present()
    if not present:
        return 0.0
    groups = components(world)
    return len(groups[0]) / len(present) if groups else 0.0


def mean_tie(world, resident_id):
    ties = [s for other, s in world.ties.get(resident_id, {}).items()
            if world.residents[other]["status"] == "present"]
    return fmean(ties) if ties else 0.0


def strong_ties(world, resident_id, threshold=0.35, locals_only=False):
    return sum(1 for other, s in world.ties.get(resident_id, {}).items()
               if s >= threshold and world.residents[other]["status"] == "present"
               and not (locals_only and world.residents[other]["newcomer"]))


def networth(world):
    stores = world.stores
    food = stores["granary"] + sum(r["food"] for r in world.present())
    coins = stores["treasury"] + sum(r["coins"] for r in world.present())
    medicine = stores["medicine"] + sum(r.get("medicine", 0) for r in world.present())
    tools = stores["tools"]
    food_price = world.recipe["world"]["prices"]["food"]
    return (coins + food * food_price + medicine * REFERENCE_PRICES["medicine"]
            + tools * REFERENCE_PRICES["tools"])


def skill_levels(world):
    best = {skill: 0 for skill in SKILLS}
    for r in world.present():
        for skill, level in r["skills"].items():
            best[skill] = max(best[skill], level)
    return best


def successor_depth(world):
    """Per skill, the level that would survive the loss of its best holder: the second-best
    holder or a written manual (Henrich 2004: small groups lose what too few people know)."""
    levels = {skill: [] for skill in SKILLS}
    for r in world.present():
        for skill, level in r["skills"].items():
            levels[skill].append(level)
    depth = {}
    for skill, values in levels.items():
        values.sort(reverse=True)
        second = values[1] if len(values) > 1 else 0
        depth[skill] = max(second, world.manuals.get(skill, 0))
    return depth


def integrated(world, r):
    return (r["status"] == "present" and strong_ties(world, r["id"], 0.3, locals_only=True) >= 2
            and r["wellbeing"] >= 5)


def snapshot(world):
    """Public and hidden aggregates at the current moment."""
    present = world.present()
    n = len(present)

    def mean(key):
        return fmean(r[key] for r in present) if present else 0.0

    newcomers = [r for r in world.residents.values() if r["newcomer"] and r["status"] != "dead"]
    best = skill_levels(world)
    beliefs = {}
    for fact in world.facts.values():
        if fact["status"] == "live":
            aware = [r["beliefs"][fact["id"]] for r in present if fact["id"] in r["beliefs"]]
            correct = sum(1 for p in aware if (p >= 0.7) == fact["truth"] and (p >= 0.7 or p <= 0.3))
            beliefs[fact["id"]] = dict(mean=fmean(aware) if aware else 0.5, aware=len(aware) / n if n else 0,
                                       informed=correct / n if n else 0)
    conventions = {}
    for domain, variant in world.conventions["required"].items():
        conventions[domain] = (sum(1 for r in present if r["convention"].get(domain) == variant) / n) if n else 0
    return dict(
        week=world.week,
        population=n,
        population_share=n / max(1, world.initial_population),
        granary=world.stores["granary"],
        treasury=world.stores["treasury"],
        medicine=world.stores["medicine"],
        tools=world.stores["tools"],
        commons=world.commons["stock"] / world.commons["capacity"],
        harvest=world.commons["last_harvest"],
        trust=mean("trust"),
        legitimacy=mean("legit"),
        wellbeing=mean("wellbeing"),
        stress=mean("stress"),
        loneliness=mean("loneliness"),
        grievance=mean("grievance"),
        gini=gini([r["coins"] for r in present]),
        protest=(sum(r["protesting"] for r in present) / n) if n else 0,
        starving=(sum(r["hungry"] for r in present) / n) if n else 0,
        sick=(sum(r["sick"] for r in present) / n) if n else 0,
        cohesion=cohesion(world),
        networth=networth(world),
        integration=(sum(integrated(world, r) for r in newcomers) / len(newcomers)) if newcomers else 0,
        newcomers=len([r for r in newcomers if r["status"] == "present"]),
        deaths=sum(r["status"] == "dead" for r in world.residents.values()),
        departures=sum(r["status"] == "departed" for r in world.residents.values()),
        skills=best,
        beliefs=beliefs,
        conventions=conventions,
        distressed=sum(1 for r in present if r["wellbeing"] < 4),
        isolated=sum(1 for r in present if r["loneliness"] > 0.6),
    )


def metric(world, name):
    """A named reading for story conditions (see catalog.METRICS)."""
    head, _, key = name.partition(":")
    if not key:
        if name == "week":
            return world.week
        data = world.snapshot()
        return float(data[name])
    if head == "belief":
        values = [r["beliefs"][key] for r in world.present() if key in r["beliefs"]]
        return fmean(values) if values else 0.5
    if head == "informed":
        return world.snapshot()["beliefs"].get(key, {}).get("informed", 0.0)
    if head == "skill":
        return float(skill_levels(world).get(key, 0))
    if head == "holders":
        return float(sum(1 for r in world.present() if r["skills"].get(key, 0) >= 2))
    if head == "project":
        return 1.0 if world.projects.get(key, {}).get("status") == "done" else 0.0
    if head == "convention":
        return float(world.snapshot()["conventions"].get(key, 0.0))
    if head == "policy":
        value = world.policies.get(key)
        return -1.0 if value is None else float(value) if not isinstance(value, str) else float(
            hash_choice(key, value))
    if head == "conflict":
        conflict = world.conflicts.get(key)
        return -1.0 if conflict is None or conflict["resolved"] else conflict["intensity"]
    if head == "actions":
        return float(world.counters.get(key, 0))
    resident = world.residents.get(key)
    if resident is None:
        return 0.0
    if head == "alive":
        return 0.0 if resident["status"] == "dead" else 1.0
    if head == "present":
        return 1.0 if resident["status"] == "present" else 0.0
    if head in ("health", "wellbeing", "trust", "loneliness"):
        return float(resident[head])
    return 0.0


def hash_choice(key, value):
    from .catalog import POLICIES

    spec = POLICIES.get(key, {})
    choices = spec.get("choices")
    if choices and value in choices:
        return choices.index(value)
    return 1 if value else 0
