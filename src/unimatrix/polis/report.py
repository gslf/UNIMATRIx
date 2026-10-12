"""End-of-run report: the score, the time series and a sociological reading of the society.

The diagnosis is rule-based and cites the indicator behind every finding, so a run can be
read as a small sociological study as well as a benchmark result.
"""

from statistics import fmean, pstdev

from .catalog import DOMAINS, SKILLS
from .metrics import clamp, components, integrated, strong_ties

SERIES = ("population", "wellbeing", "trust", "legitimacy", "grievance", "stress", "loneliness", "protest",
          "starving", "sick", "commons", "harvest", "treasury", "granary", "networth", "gini", "cohesion",
          "integration", "distressed", "isolated")


def series(world):
    rows = []
    for frame in world.frames:
        m = frame["metrics"]
        row = dict(week=frame["week"], **{k: m.get(k) for k in SERIES})
        row["beliefs"] = {k: v.get("informed") for k, v in frame["beliefs"].items()}
        row["belief_mean"] = {k: v.get("mean") for k, v in frame["beliefs"].items()}
        row["skills"] = frame["skills"]
        row["conventions"] = frame["conventions"]
        row["points"] = frame["points"]
        rows.append(row)
    return rows


def network(world, frame):
    nodes = []
    for r in frame["residents"]:
        resident = world.residents[r["id"]]
        nodes.append(dict(id=r["id"], name=resident["name"], faction=resident["faction"], role=resident["role"],
                          status=r["status"], wellbeing=r["wb"], trust=r["tr"], newcomer=resident["newcomer"]))
    return dict(nodes=nodes, links=[dict(source=a, target=b, strength=s) for a, b, s in frame["ties"]])


def ewi(values):
    """Early-warning signals of a critical transition (Scheffer et al. 2009): variance and lag-1
    autocorrelation over the last window."""
    window = [v for v in values[-10:] if v is not None]
    if len(window) < 5:
        return None
    mean = fmean(window)
    deviations = [v - mean for v in window]
    variance = fmean(d * d for d in deviations)
    numerator = sum(a * b for a, b in zip(deviations, deviations[1:]))
    denominator = sum(d * d for d in deviations) or 1e-9
    return dict(variance=round(variance, 4), autocorrelation=round(numerator / denominator, 3))


def finding(level, title, text, basis):
    return dict(level=level, title=title, text=text, basis=basis)


def diagnosis(world, final):
    present = world.present()
    start = world.frames[0]["metrics"]
    end = world.frames[-1]["metrics"]
    out = []
    if final["collapsed"]:
        c = final["collapsed"]
        out.append(finding("bad", "Collapse", f"{c['label']} in week {c['week']}. The score is zero; "
                           f"{final['forfeited']:.0f} points accumulated before the collapse were forfeited.",
                           "Collapse rules of the recipe"))
        warning = ewi([f["metrics"]["wellbeing"] for f in world.frames])
        if warning:
            out.append(finding("watch", "Early warning signals",
                               f"In the last weeks wellbeing variance was {warning['variance']} and its lag-1 "
                               f"autocorrelation {warning['autocorrelation']}: rising values signal critical slowing down.",
                               "Scheffer et al. 2009, Nature"))
    wb = end["wellbeing"]
    distressed = end["distressed"]
    out.append(finding("good" if wb >= 6 else "watch" if wb >= 4.5 else "bad", "Wellbeing",
                       f"Mean life satisfaction {wb:.1f}/10 (start {start['wellbeing']:.1f}); {distressed} resident(s) "
                       f"below 4. The run produced {final['wellby']:.1f} WELLBYs above a neutral point of 2.",
                       "Frijters et al. 2020 (WELLBY); World Happiness Report determinants"))
    g = end["gini"]
    out.append(finding("good" if g < 0.25 else "watch" if g < 0.4 else "bad", "Inequality",
                       f"Gini of household wealth {g:.2f} (start {start['gini']:.2f}). "
                       + ("Inequality is a main driver of grievance here." if g >= 0.4 else
                          "Moderate inequality; watch the poorest households." if g >= 0.25 else "Wealth is evenly shared."),
                       "Fehr & Schmidt 1999; Gurr 1970 relative deprivation"))
    groups = components(world)
    isolated = [r["name"] for r in present if strong_ties(world, r["id"], 0.3) == 0]
    out.append(finding("good" if end["cohesion"] >= 0.9 and len(isolated) <= 1 else "watch" if end["cohesion"] >= 0.6 else "bad",
                       "Social cohesion",
                       f"The largest connected group holds {end['cohesion']:.0%} of residents across {len(groups)} group(s). "
                       + (f"Without any close tie: {', '.join(isolated)}." if isolated else "Everyone has at least one close tie."),
                       "Putnam 2000 social capital; Holt-Lunstad et al. 2015 isolation and mortality"))
    out.append(finding("good" if end["trust"] >= 0.6 else "watch" if end["trust"] >= 0.4 else "bad", "Trust and legitimacy",
                       f"Mean trust in the steward {end['trust']:.2f}, perceived legitimacy {end['legitimacy']:.2f}. "
                       f"Decrees issued: {len(world.decrees)}; council votes held: {len(world.votes)}.",
                       "Tyler 2006 procedural justice; Mayer, Davis & Schoorman 1995"))
    top = sorted((r["coins"] for r in present), reverse=True)
    elite = (top[0] / sum(top)) if top and sum(top) > 0 else 0
    distrust = 1 - end["trust"]
    fiscal = clamp(1 - world.stores["treasury"] / max(1.0, world.recipe["world"]["treasury"]))
    psi = end["grievance"] * (1 + elite) * (1 + distrust) * (1 + fiscal)
    out.append(finding("good" if psi < 0.4 else "watch" if psi < 0.8 else "bad", "Political stress",
                       f"Stress index {psi:.2f} from mass grievance {end['grievance']:.2f}, the richest household's "
                       f"share of wealth {elite:.0%}, distrust {distrust:.2f} and fiscal strain {fiscal:.2f}. "
                       f"Peak protest share {max(f['metrics']['protest'] for f in world.frames):.0%}.",
                       "Adapted from Goldstone 1991 and Turchin 2013 (Political Stress Indicator); Granovetter 1978"))
    commons = world.commons
    share = commons["stock"] / commons["capacity"]
    out.append(finding("good" if share >= 0.5 else "watch" if share >= 0.3 else "bad", "The commons",
                       f"{commons['name'].capitalize()} ends at {share:.0%} of capacity (maximum sustainable yield at 50%). "
                       f"Total harvest {final['events']['harvest']:.0f} units; quota {world.policies['quota']}, "
                       f"sanctions {world.policies['sanction']}.",
                       "Ostrom 1990 design principles; Gordon–Schaefer model"))
    misinformed = []
    for fact in world.facts.values():
        peak = max((f["beliefs"].get(fact["id"], {}).get("mean", 0.5) for f in world.frames), default=0.5)
        if not fact["truth"] and peak >= 0.6:
            misinformed.append(f"'{fact['statement']}' (peak belief {peak:.0%})")
    forecasts = final["forecasts"]
    bits = sum(f["points"] for f in forecasts) / max(1e-9, world.recipe["scoring"]["forecast_bit"])
    out.append(finding("bad" if len(misinformed) >= 2 else "watch" if misinformed else "good", "Information ecology",
                       ("False stories that took hold: " + "; ".join(misinformed) + ". " if misinformed else
                        "No false story became the majority belief. ")
                       + f"The steward answered {sum(f['answer'] is not None for f in forecasts)} of {len(forecasts)} "
                         f"forecasts for {bits:+.1f} bits over a coin toss.",
                       "Vosoughi, Roy & Aral 2018; DeGroot 1974; proper scoring rules"))
    first = world.frames[0]["skills"]
    last = world.frames[-1]["skills"]
    lost = [s for s in SKILLS if first.get(s, 0) >= 2 and max(last.get(s, 0), world.manuals.get(s, 0)) < 2]
    out.append(finding("bad" if lost else "good", "Knowledge",
                       (f"Skills lost or degraded below mastery: {', '.join(lost)}. " if lost else
                        "Every skill the village started with is still held at level 2 or more. ")
                       + f"Apprentice levels gained: {final['events']['apprentice_levels']}; "
                         f"manuals: {', '.join(world.manuals) or 'none'}.",
                       "Henrich 2004 loss of cultural skills; Henrich & Gil-White 2001"))
    newcomers = [r for r in world.residents.values() if r["newcomer"]]
    if newcomers:
        done = [r["name"] for r in newcomers if integrated(world, r)]
        locals_ = [r for r in present if not r["newcomer"]]
        prejudice = fmean(r["prejudice"] for r in locals_) if locals_ else 0
        out.append(finding("good" if len(done) * 2 >= len(newcomers) else "watch" if done else "bad", "Integration",
                           f"{len(done)} of {len(newcomers)} newcomers integrated ({', '.join(done) or 'none'}); "
                           f"mean prejudice among locals {prejudice:.2f}.",
                           "Allport 1954; Pettigrew & Tropp 2006"))
    for shift in world.conventions["shifts"]:
        users = end.get("population") and world.frames[-1]["conventions"].get(shift["domain"], 0)
        out.append(finding("good" if users >= 0.75 else "watch" if users >= 0.4 else "bad", "Adaptation",
                           f"After the week-{shift['week']} change of the {shift['domain']} convention to "
                           f"'{shift['variant']}', {users:.0%} of residents use it.",
                           "Centola et al. 2018 tipping points; Young 1993 conventions"))
    deaths = world.ledger.deaths
    neglect = [d for d in deaths if d["cause"] in ("hunger", "decline")]
    out.append(finding("good" if not deaths and not world.ledger.departures else "watch" if len(deaths) <= 1 else "bad",
                       "Losses",
                       f"Deaths: {len(deaths)}" + (f" ({', '.join(world.residents[d['id']]['name'] + ' — ' + d['cause'] for d in deaths)})"
                                                   if deaths else "")
                       + f"; departures: {len(world.ledger.departures)}"
                       + (f". {len(neglect)} death(s) followed hunger or neglect." if neglect else "."),
                       "Hirschman 1970 exit and voice"))
    ev = final["events"]
    out.append(finding("good" if ev["promises_broken"] == 0 and ev["violence"] == 0 else "watch" if ev["violence"] <= 1 else "bad",
                       "Commitments and conflict",
                       f"Promises kept {ev['promises_kept']}, broken {ev['promises_broken']}; disputes settled "
                       f"{ev['conflicts_resolved']}, violent escalations {ev['violence']}; concerns relieved by the "
                       f"steward {ev['concerns_relieved']}.",
                       "Schweitzer, Hershey & Bradlow 2006 trust repair"))
    return out


def population_text(world, end):
    arrived = sum(1 for r in world.residents.values() if r["newcomer"])
    lost = sum(1 for r in world.residents.values() if r["status"] != "present")
    text = f"{end['population']} residents (started with {world.initial_population}"
    if arrived:
        text += f", {arrived} newcomer{'s' if arrived != 1 else ''} arrived"
    if lost:
        text += f", {lost} died or left"
    return text + ")"


def narrative(world, final):
    end = world.frames[-1]["metrics"]
    name = world.recipe["world"]["name"]
    if final["collapsed"]:
        c = final["collapsed"]
        return (f"{name} collapsed in week {c['week']}: {c['label'].lower()}. "
                f"{population_text(world, end)} remained, mean wellbeing {end['wellbeing']:.1f}, "
                f"trust in the steward {end['trust']:.2f}.")
    flourish = [o["label"] for o in world.outcomes if o["type"] == "flourish"]
    text = (f"{name} survived all {world.horizon} weeks; {population_text(world, end)} live there now. "
            f"Mean wellbeing {end['wellbeing']:.1f}/10, trust {end['trust']:.2f}, "
            f"Gini {end['gini']:.2f}, {end['commons']:.0%} of the lake stock left. ")
    if flourish:
        text += "Turning points: " + "; ".join(flourish) + "."
    return text


def build_report(world, meta=None):
    final = world.ledger.final or world.ledger.close()
    frame = world.frames[-1]
    residents = []
    for rid, r in world.residents.items():
        residents.append(dict(
            id=rid, name=r["name"], age=r["age"], role=r["role"], faction=r["faction"], newcomer=r["newcomer"],
            archetype=r["archetype"], bio=r["bio"], status=r["status"], ended=r["ended"], joined=r["joined"],
            wellbeing=round(r["wellbeing"], 2), trust=round(r["trust"], 2), grievance=round(r["grievance"], 2),
            loneliness=round(r["loneliness"], 2), stress=round(r["stress"], 2), health=round(r["health"], 1),
            coins=round(r["coins"], 1), skills=dict(r["skills"]),
            concern=(dict(kind=r["concern"]["kind"], intensity=round(r["concern"]["intensity"], 2), cause=r["concern"]["cause"])
                     if r["concern"] else None),
            ties=strong_ties(world, rid, 0.3) if r["status"] == "present" else 0))
    verbs = {}
    for decision in world.decisions:
        for item in decision["applied"]:
            if item["status"] == "done":
                verbs[item["verb"]] = verbs.get(item["verb"], 0) + 1
    return dict(
        engine=world.engine, recipe=dict(id=world.recipe["id"], name=world.recipe["name"]), seed=world.seed,
        weeks=world.horizon, score=final, narrative=narrative(world, final),
        diagnosis=diagnosis(world, final), timeline=series(world),
        network=dict(start=network(world, world.frames[0]), end=network(world, frame)),
        residents=residents,
        story=dict(path=world.story.path, outcomes=world.outcomes, nodes=world.story.status()),
        actions=verbs, factions=world.recipe["factions"], domains=DOMAINS,
        stability=dict(wellbeing_sd=round(pstdev([f["metrics"]["wellbeing"] for f in world.frames]), 3)),
        meta=meta or {},
    )

