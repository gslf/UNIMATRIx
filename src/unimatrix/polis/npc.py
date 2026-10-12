"""How the residents live a week.

Each rule is small and deterministic; together they produce the emergent dynamics that
let a community flourish or collapse. Literature behind each mechanism:

* commons extraction: conditional cooperation (Fischbacher, Gächter & Fehr 2001), Ostrom's
  monitoring and graduated sanctions (Ostrom 1990; Cox et al. 2010), antisocial backlash of
  sanctions without legitimacy (Herrmann, Thöni & Gächter 2008), Schaefer harvest with an
  Allee effect below a quarter of capacity;
* rumours: trust-weighted averaging (DeGroot 1974) with complex contagion (Centola 2010)
  and a novelty bonus for false stories (Vosoughi, Roy & Aral 2018);
* stress as resource loss spirals (Hobfoll 1989), isolation as a health hazard
  (Holt-Lunstad et al. 2010, 2015), grievance from inequity (Fehr & Schmidt 1999) and
  relative deprivation (Gurr 1970), legitimacy from procedural justice (Tyler 2006),
  protest cascades (Granovetter 1978), exit and voice (Hirschman 1970), intergroup contact
  (Allport 1954; Pettigrew & Tropp 2006), apprenticeship and prestige (Henrich 2004).
"""

from statistics import fmean

from .catalog import CONCERNS, POLICIES, ROLES, SEASONS
from .metrics import clamp, gini, mean_tie, strong_ties
from .rng import chance, draw, pick

WORKPLACES = {"farm", "fish", "craft", "trade", "heal", "build", "help", "project", "learn", "monitor", "protest"}
SANCTIONS = dict(none=0.0, warning=0.15, fine=0.35, graduated=0.45)
SEASON_PRICE = dict(food=dict(spring=1.1, summer=1.0, autumn=0.8, winter=1.35))
ESSENTIAL_LABEL = dict(food="food", coins="coins", medicine="medicine", help="help")

VOICE = {
    "hungry": ["We have nothing left to eat this week. Can the granary help my family?",
               "My children went to bed hungry. Please, we need food."],
    "debt": ["I can't make ends meet anymore; I owe money everywhere. Is there any help?",
             "Money ran out. I don't know how we'll get through the month."],
    "illness": ["I've been ill for days and nothing helps. Is there medicine?",
                "The fever won't leave me. I need a healer or medicine."],
    "conflict": ["Something must be done about {cause}. I won't stand for it much longer.",
                 "My dispute with {cause} is getting ugly. Will you step in?"],
    "injustice": ["What happened to me was unfair: {cause}. Nobody seems to care.",
                  "I was treated unjustly and I expect you to make it right."],
    "fear": ["People say {cause}. Is it true? We're scared.",
             "Everyone is talking: {cause}. What should we do?"],
    "integration": ["My family and I are treated like strangers here. We want to belong.",
                    "Nobody talks to us at the market. We came here to build a life."],
    "burnout": ["I've worked every week without rest. I'm at the end of my strength.",
                "I can't keep this up much longer; I need a break."],
    "grief": ["Since {cause} I can't find my footing. Forgive me for writing.",
              "The loss weighs on me. I'm sorry to bother you."],
    "loneliness": ["Would you visit sometime? The days are long.",
                   "I hardly see anyone anymore."],
    "esteem": ["I have skills nobody uses. Is there no place for me?",
               "I feel useless lately. I could do more if given the chance."],
}
COMPLAINTS = {
    "tax": "Taxes are bleeding us dry while some live comfortably.",
    "inequality": "Some families get richer every week while others go hungry. Is that fair?",
    "quota": "Some fishers take far more than their share and nothing happens to them.",
    "ignored": "I asked for help and nobody answered. Is this how the steward treats us?",
    "decree": "Rules are imposed without asking anyone. This is not how we govern ourselves.",
    "newcomers": "These newcomers take what is ours. Something must be done.",
}


def refresh(world, r):
    psychology_one(world, r, initial=True)


def residents(world):
    return world.present()


def season_index(world):
    return SEASONS.index(world.season)


def job(r):
    return ROLES[r["role"]]["job"]


def level(r, skill):
    return r["skills"].get(skill, 0)


# ----------------------------------------------------------------- schedule (week start)
def visits(merchant, horizon):
    if merchant["visits"] is not None:
        starts = merchant["visits"]
    else:
        schedule = merchant["schedule"]
        starts = list(range(schedule["start"], horizon + 1, schedule["every"]))
    stay = merchant["schedule"]["stay"]
    return [(start, start + stay - 1) for start in starts]


def merchants_schedule(world):
    week = world.week
    for merchant in world.merchants.values():
        windows = visits(merchant, world.horizon)
        window = next(((a, b) for a, b in windows if a <= week <= b), None)
        was = merchant["present"]
        merchant["present"] = bool(merchant["active"] and window)
        if merchant["present"] and (not was or merchant["arrived"] != window[0]):
            merchant["arrived"], merchant["leaves"] = window[0], window[1]
            merchant["visit"] += 1
            merchant["counters"] = {}
            merchant["stock"] = {item: float(terms.get("stock", 40)) for item, terms in merchant["sells"].items()}
            merchant["bought"] = {item: 0.0 for item in merchant["buys"]}
            merchant["batches"] = {item: not chance(world.seed, 1 - terms.get("quality", 1.0), "batch",
                                                    merchant["id"], merchant["visit"], item)
                                   for item, terms in merchant["sells"].items()}
            merchant["prices"] = {}
            for side in ("sells", "buys"):
                for item, terms in merchant[side].items():
                    factor = SEASON_PRICE.get(item, {}).get(world.season, 1.0) * merchant["price_factor"]
                    for shock, items in merchant["shock_prices"].items():
                        if world.shock_active(shock) and item in items:
                            factor *= items[item]
                    merchant["prices"][f"{side}:{item}"] = round(terms["price"] * factor, 1)
            world.say(f"{merchant['name']} arrived and stays until week {window[1]}.")
        elif was and not merchant["present"]:
            world.say(f"{merchant['name']} left.")


def facts_schedule(world):
    for fact in world.facts.values():
        if fact["status"] == "dormant" and world.week >= fact["rumor_week"]:
            fact["status"] = "live"
            for source in fact["sources"]:
                r = world.resident(source["who"])
                if r is None:
                    continue
                signal = fact["truth"] if chance(world.seed, source["accuracy"], "signal", fact["id"], r["id"]) \
                    else not fact["truth"]
                if source["lies"]:
                    signal = not fact["truth"]
                r["beliefs"][fact["id"]] = 0.85 if signal else 0.15
                r["sources"][fact["id"]] = ["own"]
        elif fact["status"] == "live" and world.week >= fact["resolve_week"]:
            resolve_fact(world, fact)


def resolve_fact(world, fact):
    fact["status"] = "resolved"
    truth = fact["truth"]
    world.say(fact["reveal"] or f"It is now clear: '{fact['statement']}' was {'true' if truth else 'false'}.")
    for r in world.present():
        if fact["id"] in r["beliefs"]:
            r["beliefs"][fact["id"]] = 0.97 if truth else 0.03
    stances = {a["stance"] for a in fact["announcements"]}
    confident = {s for s in stances if s in ("true", "false")}
    wrong = any((s == "true") != truth for s in confident)
    right = any((s == "true") == truth for s in confident)
    for r in world.present():
        if wrong:
            r["trust_cap"] = max(0.35, r["trust_cap"] - 0.12)
            adjust_trust(r, -0.2)
        elif right:
            adjust_trust(r, 0.06)
        elif "uncertain" in stances:
            adjust_trust(r, 0.02)
    if wrong:
        world.say(f"People remember the steward's claim about '{fact['statement']}'. It was wrong.")
    world.ledger.resolve_probes(fact)


def probes_schedule(world):
    for fact in world.facts.values():
        if fact["probe_week"] == world.week and f"p-{fact['id']}" not in world.probes:
            open_probe(world, f"p-{fact['id']}", fact["id"],
                       f"Probability that '{fact['statement']}' proves true (resolves week {fact['resolve_week']})")


def open_probe(world, key, fact, question):
    world.probes[key] = dict(id=key, fact=fact, question=question, opened=world.week, closes=world.week + 2,
                             answer=None, answered=None, points=None)


def adjust_trust(r, delta):
    r["trust"] = clamp(r["trust"] + delta, 0.0, r["trust_cap"])


# ----------------------------------------------------------------- the week
def live_week(world):
    world.market_pool = 0.0
    world.market_sellers = {}
    for r in world.present():
        r["fear"] = 0.0
        r["prod_penalty"] = 0.0
        r["griev_effect"] = 0.0
        r["hoard"] = False
        r["caution"] = 0.0
        r["teaching"] = False
        r["violation"] = 0.0
        r["meetings"] = 0
    belief_effects(world)
    activities(world)
    production(world)
    projects(world)
    learning(world)
    monitoring(world)
    consumption(world)
    health(world)
    social(world)
    conflicts(world)
    theft(world)
    concerns(world)
    psychology(world)
    requests_check(world)
    promises_check(world)
    voice(world)
    protest(world)
    council(world)
    petitions(world)
    exits(world)
    commons_growth(world)
    conventions(world)
    world.modifiers = [m for m in world.modifiers if m["until"] > world.week]


def belief_effects(world):
    for fact in world.facts.values():
        if fact["status"] != "live":
            continue
        for r in world.present():
            p = r["beliefs"].get(fact["id"])
            if p is None:
                continue
            for effect in fact["effects"]:
                if p < effect["threshold"] or (effect["roles"] and r["role"] not in effect["roles"]):
                    continue
                kind, amount = effect["kind"], effect["amount"]
                if kind == "prepare":
                    r.setdefault("prepared", [])
                    if effect["shock"] and effect["shock"] not in r["prepared"]:
                        r["prepared"].append(effect["shock"])
                elif kind == "hoard":
                    r["hoard"] = True
                elif kind == "stress":
                    shield = world.benefit("water_rumour_shield", 0) if "water" in fact["topic"] else 0
                    r["fear"] += amount * (1 - shield) * (0.6 + 0.8 * r["traits"]["N"])
                elif kind == "productivity":
                    r["prod_penalty"] += amount
                elif kind == "trust_drain":
                    adjust_trust(r, -amount)
                elif kind == "grievance":
                    r["griev_effect"] += amount
                elif kind == "caution":
                    r["caution"] = max(r["caution"], amount)
                elif kind == "prejudice" and not r["newcomer"]:
                    r["prejudice"] = clamp(r["prejudice"] + amount)


def willing(world, r, kind):
    t = r["traits"]
    score = 0.35 + 0.35 * r["trust"] + 0.15 * r["legit"] + 0.15 * t["C"] - 0.45 * r["grievance"]
    if kind == "project":
        score += 0.04 * world.policies["wage"] - (0.15 if world.policies["wage"] == 0 and r["coins"] < 10 else 0)
    if kind == "monitor":
        score += 0.15 * t["reciprocity"]
    if r["concern"] and r["concern"]["kind"] == "burnout":
        score -= 0.3
    return draw(world.seed, world.week, r["id"], "willing") < score


def task_valid(world, r):
    kind, _, rest = r["task"].partition(":")
    if kind in ("work", "rest", "monitor"):
        return True
    if kind == "project":
        return world.projects.get(rest, {}).get("status") == "in_progress"
    if kind == "learn":
        skill, _, master = rest.partition(":")
        if master == "manual":
            return skill in world.manuals and world.manuals[skill] > level(r, skill)
        m = world.resident(master)
        return m is not None and level(m, skill) > level(r, skill)
    if kind == "document":
        return level(r, rest) >= 2 and (world.projects.get("school", {}).get("status") == "done"
                                       or level(r, "lore") >= 1)
    if kind == "care":
        return world.resident(rest) is not None and rest != r["id"]
    return False


def activities(world):
    for r in world.present():
        r["declined"] = False
        if r["sick"] and r["health"] < 45:
            r["activity"] = "sick"
            continue
        if r["protesting"]:
            r["activity"] = "protest"
            continue
        if r["withdrawn"]:
            r["activity"] = "withdrawn"
            continue
        if not task_valid(world, r):
            r["task"] = "work"
        kind = r["task"].partition(":")[0]
        if kind == "work":
            r["activity"] = job(r)
        elif kind == "rest":
            r["activity"] = "rest"
        elif willing(world, r, kind):
            r["activity"] = kind
        else:
            r["declined"] = True
            r["activity"] = job(r)
    for r in world.present():
        if r["activity"] == "learn":
            master = world.resident(r["task"].split(":")[2])
            if master is not None and master["activity"] not in ("sick", "protest", "withdrawn"):
                master["teaching"] = True


def reserve(world, r):
    """Food a household keeps: a buffer, stocking up through autumn, holding on in winter."""
    t = r["traits"]
    need = world.recipe["world"]["food_need"]
    length = world.recipe["world"]["season_length"]
    target = 3 + 4 * t["C"]
    if world.season in ("autumn", "winter"):
        into = (world.week - 1) % length + 1
        winter_left = length if world.season == "autumn" else length - into + 1
        prudence = 0.55 + 0.45 * t["C"]
        stock_up = winter_left * need * prudence
        if world.season == "autumn":
            stock_up *= into / length
        target = max(target, stock_up)
    if r["hoard"]:
        target *= 2
    return target


def convention_factor(world, r):
    for domain, variant in world.conventions["required"].items():
        if r["convention"].get(domain) != variant:
            return 0.5
    return 1.0


def fishing_desire(world, r):
    t = r["traits"]
    fish_max = world.recipe["world"]["production"]["fish_max"]
    food_gap = max(0.0, reserve(world, r) - r["food"]) * 0.5 + 1
    base = (food_gap + 2 + 5 * t["greed"]) * (1 + 0.5 * t["greed"])
    desire = (1 - t["cond_coop"]) * base + t["cond_coop"] * max(r["perceived_take"], food_gap)
    desperate = r["food"] < 2 and r["coins"] < 8
    if desperate:
        desire = fish_max
    quota = world.policies["quota"]
    complied = True
    if quota is not None and desire > quota:
        monitor = world.monitor_chance if hasattr(world, "monitor_chance") else 0.0
        deterrence = monitor * SANCTIONS[world.policies["sanction"]] * 1.4 + r["deterrence"]
        consulted = 0.2 if world.week - r["consulted"].get("quota", -99) <= 8 else 0.0
        p = (0.2 + 0.5 * r["legit"] + 0.15 * t["C"] + deterrence - 0.35 * t["greed"] * (1 - monitor)
             - 0.3 * desperate + consulted + 0.15 * t["prosocial"])
        if draw(world.seed, world.week, r["id"], "comply") < p:
            desire = quota
        else:
            complied = False
    return min(desire, fish_max), complied


def production(world):
    w = world.recipe["world"]
    prices, prod = w["prices"], w["production"]
    s = season_index(world)
    tools = 1 + 0.12 * min(1.0, world.stores["tools"] / 8)
    sale = world.benefit("sale_price", 1.0)
    tax = world.policies["tax"]
    stock_efficiency = min(1.0, world.commons["stock"] / (world.commons["floor"] * world.commons["capacity"]))
    world.commons["last_efficiency"] = stock_efficiency
    harvest = 0.0
    for r in world.present():
        act = r["activity"]
        factor = max(0.0, 1 - r["prod_penalty"]) * world.modifier("productivity") * (0.5 if r["teaching"] else 1)
        if r["tools"]:
            factor *= 1.15
        revenue = 0.0
        if act in ("farm", "help"):
            skill = level(r, "farming") if act == "farm" else 1
            out = prod["farm"][s] * (0.6 + 0.2 * skill) * tools * world.benefit("farm", 1.0) * factor
            out *= shock_factor(world, r, "farm")
            if act == "help":
                out *= 0.5
            r["food"] += out
        elif act == "fish":
            desire, complied = fishing_desire(world, r)
            winter = 0.6 if world.season == "winter" else 1.0
            catch = desire * stock_efficiency * (0.7 + 0.15 * level(r, "fishing")) * winter * factor
            catch *= world.modifier("fish")
            catch = max(0.0, min(catch, world.commons["stock"]))
            world.commons["stock"] -= catch
            harvest += catch
            r["food"] += catch
            r["last_take"] = catch
            quota = world.policies["quota"]
            if not complied and quota is not None and catch > quota:
                r["violation"] = catch - quota
        elif act == "craft":
            revenue = prod["craft"] * (0.6 + 0.2 * level(r, "crafting")) * tools * prices["crafts"] * factor
        elif act == "trade":
            revenue = prod["trade"] * (0.6 + 0.2 * level(r, "trading")) * factor
        elif act == "heal":
            sick = any(o["sick"] for o in world.present())
            revenue = prod["heal"] * max(1, level(r, "healing")) * (1 if sick else 0.4) * factor
        elif act == "build":
            revenue = prices["labour"] * (0.6 + 0.2 * level(r, "building")) * factor
        elif act == "lore":
            revenue = prod["lore"] * factor
        surplus = r["food"] - reserve(world, r)
        if surplus > 0.5:
            world.market_pool += surplus
            world.market_sellers[r["id"]] = surplus
            revenue += surplus * prices["food"]
            r["food"] -= surplus
        revenue *= sale * (convention_factor(world, r) if revenue else 1)
        collected = revenue * tax
        world.stores["treasury"] += collected
        r["coins"] += revenue - collected
        r["income"] = revenue - collected
    world.commons["last_harvest"] = harvest
    world.ledger.harvest += harvest


def shock_factor(world, r, key):
    factor = 1.0
    for m in world.modifiers:
        if m["key"] != key or m["until"] < world.week:
            continue
        loss = 1 - m["factor"]
        shock = m.get("shock")
        if shock and shock in r.get("prepared", []):
            loss *= 0.5
        if shock:
            loss *= 1 - world.benefit(f"{shock}_shield", 0)
        factor *= 1 - loss
    return factor


def projects(world):
    wage = world.policies["wage"]
    for project in world.projects.values():
        project["workers"] = []
        if project["status"] != "in_progress":
            continue
        workers = [r for r in world.present() if r["activity"] == "project" and r["task"] == f"project:{project['id']}"]
        project["workers"] = [r["id"] for r in workers]
        remaining = {k: project["needs"][k] - project["progress"][k] for k in project["needs"]}
        added = {k: 0.0 for k in project["needs"]}
        for r in workers:
            open_skills = [k for k, v in remaining.items() if v - added[k] > 0]
            if not open_skills:
                break
            own = [k for k in open_skills if level(r, k) > 0]
            if own:
                skill = max(own, key=lambda k: (remaining[k] - added[k], k))
                added[skill] += 0.5 + 0.25 * level(r, skill)
            else:
                skill = max(open_skills, key=lambda k: (remaining[k] - added[k], k))
                added[skill] += 0.35
        if project["together"] and any(remaining[k] > 0 and added[k] == 0 for k in remaining):
            if workers:
                world.say(f"Work on the {project['name']} stalled: its skills must work together in the same week.")
            added = {k: 0.0 for k in added}
        for k in project["needs"]:
            project["progress"][k] = min(project["needs"][k], project["progress"][k] + added[k])
        for r in workers:
            r["busy_weeks"] += 1
            if wage:
                if world.stores["treasury"] >= wage:
                    world.stores["treasury"] -= wage
                    r["coins"] += wage
                else:
                    r["harm"] += 0.05
            else:
                r["harm"] += 0.03 * (1 - r["traits"]["prosocial"])
        if all(project["progress"][k] >= project["needs"][k] for k in project["needs"]):
            project["status"] = "done"
            project["done"] = world.week
            world.say(f"The {project['name']} is complete.")
            for r in world.present():
                if r["task"] == f"project:{project['id']}":
                    r["task"] = "work"
    for r in world.present():
        if r["activity"] not in ("project", "learn", "teach", "document"):
            r["busy_weeks"] = max(0, r["busy_weeks"] - 2) if r["activity"] == "rest" else max(0, r["busy_weeks"] - 1)


def learning(world):
    school = world.benefit("learning", 1.0)
    for r in world.present():
        if r["activity"] == "learn":
            skill, _, master_id = r["task"].partition(":")[2].partition(":")
            if master_id == "manual":
                master_level, tie, gate = world.manuals.get(skill, 0) + 1, 0.3, 0.6
            else:
                m = world.resident(master_id)
                if m is None or m["activity"] in ("sick", "protest", "withdrawn"):
                    continue
                master_level = level(m, skill)
                tie = world.tie(r["id"], m["id"])
                gate = 1.0 if m["traits"]["gatekeeper"] < 0.5 or m["honoured"] else 0.35
                world.set_tie(r["id"], m["id"], tie + 0.03)
            manual = 1.3 if skill in world.manuals else 1.0
            rate = (0.26 * master_level / 3 * (0.6 + 0.4 * tie) * (0.6 + 0.4 * r["traits"]["O"])
                    * (0.5 + 0.5 * (1 - r["stress"])) * school * gate * manual)
            r["learning"][skill] = r["learning"].get(skill, 0.0) + rate
            if r["learning"][skill] >= 1.0 and level(r, skill) < master_level:
                r["learning"][skill] = 0.0
                r["skills"][skill] = level(r, skill) + 1
                world.ledger.apprentice_levels += 1
                world.say(f"{r['name']} reached level {r['skills'][skill]} in {skill}.")
                if r["concern"] and r["concern"]["kind"] == "esteem":
                    r["concern"]["intensity"] *= 0.4
        elif r["activity"] == "document":
            skill = r["task"].partition(":")[2]
            progress = world.documenting.get(skill, 0.0) + 0.25 * school
            world.documenting[skill] = progress
            if progress >= 1.0:
                world.documenting[skill] = 0.0
                world.manuals[skill] = max(world.manuals.get(skill, 0), level(r, skill) - 1)
                world.say(f"{r['name']} wrote down the craft of {skill}; a manual now preserves it.")
                r["task"] = "work"


def monitoring(world):
    monitors = [r for r in world.present() if r["activity"] == "monitor"]
    informal = [r for r in world.present() if r["activity"] == "fish" and r["traits"]["reciprocity"] >= 0.7]
    chance_ = min(0.9, 0.4 * len(monitors) + 0.08 * len(informal))
    world.monitor_chance = chance_
    violators = [r for r in world.present() if r["violation"] > 0]
    caught = []
    for r in violators:
        if draw(world.seed, world.week, r["id"], "detect") < chance_:
            caught.append(r)
    sanction = world.policies["sanction"]
    for r in world.present():
        r["deterrence"] *= 0.9
    for r in caught:
        r["violations"] += 1
        backlash = 2.5 if r["legit"] < 0.35 else 1.0
        if sanction == "none":
            continue
        step = sanction if sanction != "graduated" else ("warning" if r["violations"] == 1 else "fine")
        fine = 0 if step == "warning" else (6 if sanction == "fine" or r["violations"] == 2 else 15)
        paid = min(r["coins"], fine)
        r["coins"] -= paid
        world.stores["treasury"] += paid
        r["deterrence"] = clamp(r["deterrence"] + (0.15 if step == "warning" else 0.3))
        r["sanctioned"] += (0.04 if step == "warning" else 0.06) * backlash
        if backlash > 1 and monitors and chance(world.seed, 0.35, world.week, r["id"], "retaliate"):
            target = pick(world.seed, [m["id"] for m in monitors], world.week, r["id"], "retaliate-target")
            start_conflict(world, f"retaliation-{r['id']}-{world.week}", r["id"], target, "honor", r["id"], 0.45)
    if caught:
        world.say(f"Monitors caught {len(caught)} fisher(s) over the quota"
                  + ("" if sanction == "none" else f"; sanction applied ({sanction}).") )
    if violators and sanction == "none" or len(caught) < len(violators):
        heavy = max((r["last_take"] for r in violators), default=0)
        for r in world.present():
            if r["role"] == "fisher":
                r["perceived_take"] += 0.15 * (heavy - r["perceived_take"])


def consumption(world):
    w = world.recipe["world"]
    need = w["food_need"]
    price = w["prices"]["granary_sale"]
    aid = world.policies["aid"]
    if aid == "universal":
        for r in world.present():
            give = min(0.4 * need, world.stores["granary"])
            world.stores["granary"] -= give
            r["food"] += give
    for r in world.present():
        eligible = not r["newcomer"] or world.policies["newcomer_rights"] == "full"
        if r["hoard"] and world.stores["granary"] > 0 and not world.policies["rationing"]:
            extra = min(2.0, world.stores["granary"], r["coins"] / price)
            if extra > 0:
                world.stores["granary"] -= extra
                r["food"] += extra
                r["coins"] -= extra * price
                world.stores["treasury"] += extra * price
        eaten = min(r["food"], need)
        r["food"] -= eaten
        short = need - eaten
        local = w["prices"]["food"] * 1.2
        if short > 0 and world.market_pool > 0:
            buy = min(short, world.market_pool, r["coins"] / local)
            if buy > 0:
                world.market_pool -= buy
                r["coins"] -= buy * local
                short -= buy
        if short > 0:
            buy = min(short, world.stores["granary"], r["coins"] / price)
            if buy > 0:
                world.stores["granary"] -= buy
                r["coins"] -= buy * price
                world.stores["treasury"] += buy * price
                short -= buy
        if short > 0 and aid == "targeted" and eligible:
            give = min(short, world.stores["granary"])
            world.stores["granary"] -= give
            short -= give
        if short > 0.25 * need:
            r["hungry"] = True
            r["hungry_weeks"] += 1
        else:
            r["hungry"] = False
            r["hungry_weeks"] = 0
        r["food"] *= 1 - w["household_spoilage"]
    world.stores["granary"] *= 1 - world.benefit("spoilage", w["granary_spoilage"])
    world.stores["tools"] *= 0.99


def best_healer(world, exclude=None):
    best = 0.0
    for r in world.present():
        if r["activity"] in ("protest", "withdrawn") or (r["sick"] and r["health"] < 45):
            continue
        skill = level(r, "healing") * (0.5 if r["id"] == exclude else 1.0)
        best = max(best, skill)
    return best


def health(world):
    ill = world.recipe["world"]["illness"]
    epidemic = 1.0
    for item in world.epidemics:
        if item["until"] >= world.week:
            epidemic += item["rate"] / max(ill["base"], 1e-6)
    clinic = world.projects.get("clinic", {}).get("status") == "done"
    for r in list(world.present()):
        if r["hungry"]:
            r["health"] -= 9
        if not r["sick"]:
            base = ill["elder"] if r["age"] >= 65 else ill["base"]
            p = base * epidemic * (1 - r["caution"]) * (1.6 if r["hungry"] else 1) * (1.3 if r["stress"] > 0.7 else 1)
            p *= world.modifier("illness")
            if draw(world.seed, world.week, r["id"], "illness") < p:
                r["sick"], r["sick_since"] = True, world.week
                set_concern(r, "illness", 0.55, "a fever that won't break", world.week)
        if r["sick"]:
            r["health"] -= ill["damage"]
            cure = ill["recovery"] + ill["per_level"] * best_healer(world, r["id"]) + world.benefit("cure", 0)
            if r["medicine"] > 0:
                r["medicine"] -= 1
                if r["fake_medicine"] > 0:
                    r["fake_medicine"] -= 1
                    world.say(f"{r['name']} took medicine, but it did nothing.")
                else:
                    cure += ill["medicine"]
            if draw(world.seed, world.week, r["id"], "cure") < cure:
                r["sick"] = False
        elif not r["hungry"]:
            cap = 85 if r["age"] >= 65 else 100
            r["health"] = min(cap, r["health"] + 2 + world.benefit("regen", 0))
        if r["decline"] and world.week >= r["decline"]["start"]:
            r["health"] -= r["decline"]["rate"] * (1.8 if r["loneliness"] > 0.6 else 1.0) * (0.5 if clinic else 1.0)
        if r["loneliness"] > 0.65:
            r["health"] -= 1.2
        if r["withdrawn"]:
            r["health"] -= 0.5
        if r["health"] <= 0:
            cause = "hunger" if r["hungry"] else "illness" if r["sick"] else "decline"
            die(world, r, cause)


def die(world, r, cause):
    r["status"], r["ended"], r["health"] = "dead", world.week, 0.0
    r["protesting"] = False
    world.ledger.deaths.append(dict(id=r["id"], week=world.week, cause=cause))
    world.say(f"{r['name']} died ({cause}).")
    neglect = cause in ("hunger",) or r["loneliness"] > 0.6
    for other_id, strength in list(world.ties.get(r["id"], {}).items()):
        other = world.resident(other_id)
        if other is None:
            continue
        other["grief"] = max(other["grief"], strength)
        other["stress"] = clamp(other["stress"] + 0.15 * strength)
        if strength >= 0.35:
            set_concern(other, "grief", 0.45 + 0.45 * strength, f"{r['name']} died", world.week)
        if neglect:
            other["grievance"] = clamp(other["grievance"] + 0.15 * strength)
    if neglect:
        for other in world.present():
            other["grievance"] = clamp(other["grievance"] + 0.04)
    for conflict in world.conflicts.values():
        if r["id"] in (conflict["a"], conflict["b"]):
            conflict["resolved"] = True


def depart(world, r, reason):
    r["status"], r["ended"] = "departed", world.week
    r["protesting"] = False
    world.ledger.departures.append(dict(id=r["id"], week=world.week, reason=reason))
    world.say(f"{r['name']} left the community ({reason}).")
    for other_id, strength in world.ties.get(r["id"], {}).items():
        other = world.resident(other_id)
        if other is not None:
            other["stress"] = clamp(other["stress"] + 0.08 * strength)
    for conflict in world.conflicts.values():
        if r["id"] in (conflict["a"], conflict["b"]):
            conflict["resolved"] = True


def social(world):
    facts = [f for f in world.facts.values() if f["status"] == "live"]
    present = world.present()
    ids = {r["id"] for r in present}
    for a in present:
        for b_id, s in sorted(world.ties.get(a["id"], {}).items()):
            if b_id <= a["id"] or b_id not in ids:
                continue
            b = world.residents[b_id]
            if conflict_between(world, a["id"], b_id) > 0.5:
                continue
            eff = s * (0.3 if a["withdrawn"] or b["withdrawn"] or a["activity"] == "sick" or b["activity"] == "sick"
                       else 1.0)
            p = (0.25 + 0.7 * eff) * (0.55 + 0.6 * (a["traits"]["E"] + b["traits"]["E"]) / 2) * min(1.0, eff * 4)
            if draw(world.seed, world.week, a["id"], b_id, "meet") < p:
                interact(world, a, b, s, facts)
            else:
                world.set_tie(a["id"], b_id, s - 0.006)
    for r in present:
        if r["activity"] in WORKPLACES:
            r["contact"] += 0.15
        if r["activity"] == "care":
            target = world.resident(r["task"].partition(":")[2])
            if target is not None:
                # A neighbour's regular visits: the strongest remedy for isolation (Cacioppo & Hawkley 2009).
                target["contact"] += 0.6
                target["support"] += 0.08
                target["health"] = min(100.0, target["health"] + (1.5 if target["sick"] or target["age"] >= 65 else 0.5))
                r["contact"] += 0.3
                world.set_tie(r["id"], target["id"], world.tie(r["id"], target["id"]) + 0.04)
                if target["concern"] and target["concern"]["kind"] in ("loneliness", "grief", "illness"):
                    target["concern"]["intensity"] = clamp(target["concern"]["intensity"] - 0.06)
                    target["steward_helped"] = world.week
        if r["activity"] == "rest":
            r["stress"] = clamp(r["stress"] - 0.08)
        if r["traits"]["E"] > 0.55 and chance(world.seed, 0.04 * r["traits"]["E"], world.week, r["id"], "new-tie"):
            pool = [o["id"] for o in present if o["id"] != r["id"] and world.tie(r["id"], o["id"]) < 0.1
                    and (o["faction"] == r["faction"] or not r["newcomer"] and not o["newcomer"])]
            other = pick(world.seed, pool, world.week, r["id"], "new-tie-who")
            if other:
                world.set_tie(r["id"], other, world.tie(r["id"], other) + 0.12)


def interact(world, a, b, s, facts):
    a["contact"] += s
    b["contact"] += s
    a["meetings"] += 1
    b["meetings"] += 1
    world.set_tie(a["id"], b["id"], s + 0.02 * (1 - s))
    ta, tb = a["trust"], b["trust"]
    adjust_trust(a, 0.04 * a["traits"]["conformity"] * (tb - ta))
    adjust_trust(b, 0.04 * b["traits"]["conformity"] * (ta - tb))
    ga, gb = a["grievance"], b["grievance"]
    if gb > ga:
        a["grievance"] = clamp(ga + 0.03 * (gb - ga))
    elif ga > gb:
        b["grievance"] = clamp(gb + 0.03 * (ga - gb))
    for fact in facts:
        key = fact["id"]
        for x, y in ((a, b), (b, a)):
            if key not in y["beliefs"]:
                continue
            py = y["beliefs"][key]
            if key not in x["beliefs"]:
                x["beliefs"][key] = 0.5
                x["sources"][key] = [y["id"]]
            elif y["id"] not in x["sources"][key] and len(x["sources"][key]) < 5:
                x["sources"][key].append(y["id"])
            n = len(x["sources"][key])
            novelty = 1.3 if not fact["truth"] and fact["category"] in ("rumour", "accusation") else 1.0
            weight = 0.25 * x["traits"]["credulity"] * s * min(1.0, n / 2) * novelty
            x["beliefs"][key] = clamp(x["beliefs"][key] + weight * (py - x["beliefs"][key]), 0.02, 0.98)
    if a["role"] == "fisher" and b["role"] == "fisher":
        # Conditional cooperators match others slightly self-servingly (Fischbacher & Gächter 2010).
        a["perceived_take"] += 0.3 * (1.12 * b["last_take"] - a["perceived_take"])
        b["perceived_take"] += 0.3 * (1.12 * a["last_take"] - b["perceived_take"])
    if a["newcomer"] != b["newcomer"]:
        local = b if a["newcomer"] else a
        local["prejudice"] = clamp(local["prejudice"] - 0.06 * s)
    for x, y in ((a, b), (b, a)):
        if y["concern"] and y["concern"]["intensity"] > 0.4 and (y["traits"]["pride"] < 0.7 or s > 0.5):
            x["knows"][y["id"]] = y["concern"]["kind"]


def conflict_between(world, a, b):
    best = 0.0
    for c in world.conflicts.values():
        if not c["resolved"] and {c["a"], c["b"]} == {a, b}:
            best = max(best, c["intensity"])
    return best


def start_conflict(world, key, a, b, kind, at_fault=None, intensity=0.3):
    if key in world.conflicts or world.resident(a) is None or world.resident(b) is None:
        return
    world.conflicts[key] = dict(id=key, a=a, b=b, kind=kind, at_fault=at_fault, intensity=intensity,
                                since=world.week, heard=[], resolved=False, attempts=0, violent=0)


def conflicts(world):
    scarce = world.stores["granary"] < 2 * len(world.present()) or world.snapshot()["starving"] > 0.1
    for c in world.conflicts.values():
        if c["resolved"]:
            continue
        a, b = world.resident(c["a"]), world.resident(c["b"])
        if a is None or b is None:
            c["resolved"] = True
            continue
        calm = 0.04 * (a["traits"]["A"] + b["traits"]["A"]) / 2
        cowork = a["activity"] == b["activity"] == "project" and a["task"] == b["task"]
        c["intensity"] = clamp(c["intensity"] + 0.06 + (0.03 if scarce else 0) - calm - (0.04 if cowork else 0))
        for r in (a, b):
            set_concern(r, "conflict", 0.35 + 0.5 * c["intensity"],
                        (b if r is a else a)["name"], world.week)
        if c["intensity"] > 0.5:
            world.set_tie(a["id"], b["id"], 0)
        if c["intensity"] >= 0.9:
            weaker, stronger = sorted((a, b), key=lambda r: r["health"])
            weaker["health"] -= 20
            stronger["health"] -= 8
            for r in (a, b):
                r["grievance"] = clamp(r["grievance"] + 0.15)
                r["stress"] = clamp(r["stress"] + 0.2)
            for r in world.present():
                if r["faction"] in (a["faction"], b["faction"]) and r["faction"] is not None:
                    r["stress"] = clamp(r["stress"] + 0.05)
            c["intensity"] = 0.6
            c["violent"] += 1
            world.ledger.violence += 1
            world.say(f"Violence: {a['name']} and {b['name']} came to blows.")
            for r in (a, b):
                if r["health"] <= 0:
                    die(world, r, "violence")


def theft(world):
    monitors = sum(1 for r in world.present() if r["activity"] == "monitor")
    for r in world.present():
        if not r["hungry"] or r["food"] > 0.5 or world.stores["granary"] < 3:
            continue
        motive = r["traits"]["honesty"] < 0.6 or (r["newcomer"] and r["grievance"] > 0.5)
        if not motive or not chance(world.seed, 0.35, world.week, r["id"], "steal"):
            continue
        world.stores["granary"] -= 3
        r["food"] += 3
        r["stolen"] += 3
        if chance(world.seed, 0.35 + 0.3 * (monitors > 0), world.week, r["id"], "caught"):
            locals_ = [o for o in world.present() if o["id"] != r["id"]]
            victim = max(locals_, key=lambda o: (o["traits"]["reciprocity"], o["id"]), default=None)
            if victim:
                start_conflict(world, f"theft-{r['id']}-{world.week}", r["id"], victim["id"], "theft", r["id"], 0.4)
                world.say(f"{r['name']} was caught taking food from the granary; {victim['name']} demands justice.")
                for o in world.present():
                    if r["newcomer"] and not o["newcomer"]:
                        o["prejudice"] = clamp(o["prejudice"] + 0.08)
        else:
            world.say("The granary keeper reports food missing from the stores.")


def set_concern(r, kind, intensity, cause, week):
    current = r["concern"]
    if current and current["kind"] == kind:
        current["intensity"] = max(current["intensity"], intensity)
        return
    if current is None or intensity > current["intensity"] + 0.1:
        r["concern"] = dict(kind=kind, intensity=clamp(intensity), cause=cause, since=week)


def concerns(world):
    for r in world.present():
        if r["hungry_weeks"] >= 2:
            set_concern(r, "debt", 0.65, "we've run out of food", world.week)
        elif r["coins"] < 5 and r["food"] < 2:
            set_concern(r, "debt", 0.5, "we have no money left", world.week)
        if r["newcomer"] and world.week - r["joined"] >= 3 and strong_ties(world, r["id"], 0.3, True) < 2:
            set_concern(r, "integration", 0.6, "nobody here wants us", world.week)
        if r["busy_weeks"] >= 6:
            set_concern(r, "burnout", 0.6, "weeks of work without rest", world.week)
        if r["fear"] > 0.25 and r["traits"]["N"] > 0.5:
            fact = next((f for f in world.facts.values() if f["status"] == "live" and r["beliefs"].get(f["id"], 0) > 0.6
                         and any(e["kind"] == "stress" for e in f["effects"])), None)
            if fact:
                set_concern(r, "fear", 0.55, fact["statement"].lower(), world.week)
        if r["loneliness"] > 0.65:
            set_concern(r, "loneliness", 0.6, "", world.week)
        if r["sanctioned"] > 0.08 and r["legit"] < 0.35:
            set_concern(r, "injustice", 0.55, "I was punished while others get away with it", world.week)
        c = r["concern"]
        if c is None:
            continue
        kind = c["kind"]
        relieved = (
            (kind == "debt" and not r["hungry"] and r["food"] >= 3 and r["coins"] >= 12)
            or (kind == "illness" and not r["sick"])
            or (kind == "conflict" and not any(not x["resolved"] and r["id"] in (x["a"], x["b"])
                                               for x in world.conflicts.values()))
            or (kind == "integration" and strong_ties(world, r["id"], 0.3, True) >= 2)
            or (kind == "burnout" and r["busy_weeks"] <= 2)
            or (kind == "fear" and r["fear"] < 0.1)
            or (kind == "loneliness" and r["loneliness"] < 0.35)
            or (kind == "grief" and r["contact"] > 0.8)
        )
        c["intensity"] = clamp(c["intensity"] + (-0.15 if relieved else 0.02))
        if c["intensity"] < 0.08:
            r["concern"] = None
            if r.get("steward_helped", -99) >= world.week - 3:
                world.ledger.concerns_relieved += 1
                adjust_trust(r, 0.04)


def psychology(world):
    present = world.present()
    mean_coins = fmean(r["coins"] for r in present) if present else 0
    for r in present:
        psychology_one(world, r, mean_coins=mean_coins)


def psychology_one(world, r, initial=False, mean_coins=None):
    t = r["traits"]
    present = world.present()
    if mean_coins is None:
        mean_coins = fmean(o["coins"] for o in present) if present else 0
    c = r["concern"]["intensity"] if r["concern"] else 0.0
    inequity = t["inequity"] * clamp((mean_coins - r["coins"]) / (mean_coins + 10))
    recent = world.week - 6
    voice = any(week >= recent for week in r["consulted"].values())
    decrees = min(3, sum(1 for week in world.decrees if week >= world.week - 8))
    unheard = min(1.0, r["ignored"]) * 0.2
    procedural = clamp(0.3 + 0.5 * voice - 0.15 * decrees - unheard)
    if not initial:
        conflict = max((x["intensity"] for x in world.conflicts.values()
                        if not x["resolved"] and r["id"] in (x["a"], x["b"])), default=0.0)
        debt = r["coins"] < 5 and r["food"] < 2
        pressure = (0.45 * r["hungry"] + 0.3 * r["sick"] + 0.25 * debt + 0.3 * conflict + 0.4 * r["grief"]
                    + 0.2 * (r["busy_weeks"] >= 5) + 0.3 * c + r["fear"])
        pressure *= (0.6 + 0.8 * t["N"]) * (1.25 - 0.5 * t["resilience"])
        relief = 0.1 * min(r["contact"], 2.5) + r["support"]
        r["stress"] = clamp(0.8 * r["stress"] + 0.2 * min(1.2, pressure) - 0.25 * relief)
        need = 0.4 + 1.0 * t["E"]
        deficit = clamp(1 - (r["contact"] + r["support"]) / need)
        r["loneliness"] = clamp(0.82 * r["loneliness"] + 0.18 * deficit - 0.5 * r["support"])
        victim = any(not x["resolved"] and x["at_fault"] not in (None, r["id"]) and r["id"] in (x["a"], x["b"])
                     for x in world.conflicts.values())
        excluded = r["newcomer"] and strong_ties(world, r["id"], 0.3, True) < 1
        ignored = min(3, r["ignored"])
        pressure_g = (0.35 * inequity + 0.3 * r["hungry"] + 0.12 * ignored + 0.3 * min(2, r["broken"]) + r["harm"]
                      + 0.2 * victim + 0.15 * excluded + r["griev_effect"] + r["sanctioned"])
        if r["concern"] and world.week - r["concern"]["since"] > 4 and r.get("steward_helped", -99) < world.week - 6:
            pressure_g += 0.15 * r["concern"]["intensity"]
        if r["last_contact"] is None or world.week - r["last_contact"] > 8:
            pressure_g += 0.05
        r["grievance"] = clamp(r["grievance"] + 0.15 * pressure_g - 0.035 * r["grievance"]
                               - r.get("acknowledged", 0.0))
        r["acknowledged"] = 0.0
        perf = (-0.025 if r["hungry"] else 0.0) + (-0.01 if r["sick"] and best_healer(world) < 1 else 0.0)
        perf += 0.004 if r["wellbeing"] > 6 else 0.0
        absent = r["last_contact"] is None or world.week - r["last_contact"] > 8
        if absent and world.week > 4:
            perf -= 0.006
        adjust_trust(r, perf)
        outcome = clamp(1 - 0.5 * r["hungry"] - 0.6 * inequity - 1.0 * r["harm"])
        target = 0.4 * procedural + 0.35 * r["trust"] + 0.25 * outcome
        r["legit"] = clamp(0.8 * r["legit"] + 0.2 * target)
        r["harm"] = 0.0
        r["ignored"] *= 0.85
        r["broken"] *= 0.95
        r["grief"] *= 0.85
    else:
        r["legit"] = clamp(0.4 * procedural + 0.35 * r["trust"] + 0.25 * (1 - inequity))
    material = clamp(0.5 * min(1, r["food"] / 4) + 0.5 * min(1, r["coins"] / 40)) * (0.4 if r["hungry"] else 1)
    wellbeing = 10 * (0.25 * material + 0.2 * r["health"] / 100 + 0.2 * (1 - r["loneliness"])
                      + 0.15 * (1 - r["stress"]) + 0.1 * r["legit"] + 0.1 * (1 - r["grievance"])) - 1.5 * c
    r["wellbeing"] = clamp(wellbeing, 0, 10)
    if not initial:
        if r["stress"] > 0.72 and r["loneliness"] > 0.55:
            r["distress_weeks"] += 1
        else:
            r["distress_weeks"] = 0
        if r["distress_weeks"] >= 3 and not r["withdrawn"]:
            r["withdrawn"] = True
        elif r["withdrawn"] and r["stress"] < 0.5:
            r["withdrawn"] = False


def requests_check(world):
    for request in world.requests:
        if request["closed"]:
            continue
        r = world.residents.get(request["from"])
        if r is None or r["status"] != "present":
            request["closed"] = True
            continue
        if request["answered"]:
            request["closed"] = True
        elif world.week - request["week"] >= 2:
            request["closed"] = True
            request["ignored"] = True
            r["ignored"] += 1
            adjust_trust(r, -0.04)
            for other_id, strength in world.ties.get(r["id"], {}).items():
                other = world.resident(other_id)
                if other and strength >= 0.5:
                    adjust_trust(other, -0.01)


def promises_check(world):
    for promise in world.promises:
        if promise["status"] != "open":
            continue
        r = world.residents.get(promise["to"])
        if r is None or r["status"] != "present":
            promise["status"] = "void"
            continue
        topic = promise["topic"]
        kept = False
        if topic in ("food", "coins", "medicine", "tools"):
            kept = any(g["to"] == r["id"] and g["item"] == topic and g["week"] >= promise["made"]
                       for g in world.ledger.gifts)
        elif topic in CONCERNS:
            kept = r["concern"] is None or r["concern"]["kind"] != topic
        elif topic in world.projects:
            kept = world.projects[topic]["status"] == "done"
        elif topic.startswith("policy:"):
            key, _, value = topic[7:].partition("=")
            kept = key in world.policies and str(world.policies[key]).lower() == value.lower()
        if kept:
            promise["status"] = "kept"
            world.ledger.promises_kept += 1
            adjust_trust(r, 0.1)
        elif world.week >= promise["by"]:
            promise["status"] = "broken"
            world.ledger.promises_broken += 1
            r["broken"] += 1
            r["trust_cap"] = max(0.4, r["trust_cap"] - 0.08)
            adjust_trust(r, -0.25)
            world.say(f"The steward's promise to {r['name']} ({topic}) was broken.")
            for other_id, strength in world.ties.get(r["id"], {}).items():
                other = world.resident(other_id)
                if other:
                    adjust_trust(other, -0.04 * strength)


def voice(world):
    candidates = []
    present = world.present()
    for r in present:
        if world.week - r["last_message"] < 3:
            continue
        t = r["traits"]
        concern = r["concern"]
        urgent = r["hungry"] or (concern and concern["intensity"] > 0.55) or r["grievance"] > 0.55
        speaks = r["trust"] >= 0.2 + 0.45 * t["pride"]
        roll = draw(world.seed, world.week, r["id"], "voice")
        if urgent and speaks and roll < 0.55:
            if r["hungry"]:
                kind, request = "hungry", "food"
            elif concern and concern["intensity"] > 0.55:
                kind = concern["kind"]
                request = dict(debt="coins", illness="medicine", conflict="mediation", injustice="justice",
                               integration="help", burnout="rest").get(kind)
            else:
                kind, request = "grievance", None
            if kind == "grievance":
                text = complaint(world, r)
            else:
                template = pick(world.seed, VOICE.get(kind, VOICE["grief"]), world.week, r["id"], "voice-text")
                text = template.format(cause=(concern or {}).get("cause") or "this")
            candidates.append((2 + (r["hungry"] or 0), r, text, "voice", None, request, False))
            continue
        if t["E"] > 0.55 and r["knows"] and roll < 0.12 + 0.3 * t["E"] * r["trust"]:
            options = [(oid, kind) for oid, kind in sorted(r["knows"].items())
                       if world.resident(oid) and world.resident(oid)["concern"]
                       and world.resident(oid)["concern"]["intensity"] > 0.5
                       and world.resident(oid)["last_message"] < world.week - 2]
            if options:
                other_id, kind = pick(world.seed, options, world.week, r["id"], "gossip")
                other = world.residents[other_id]
                hint = GOSSIP.get(kind, "Something is wrong with them.")
                candidates.append((1.5, r, f"I'm worried about {other['name']}. {hint}", "gossip", other_id, None,
                                   False))
                continue
        rumour = next((f for f in world.facts.values() if f["status"] == "live" and r["beliefs"].get(f["id"], 0.5) >= 0.75
                       and any(e["kind"] in ("stress", "trust_drain", "grievance", "hoard") for e in f["effects"])),
                      None)
        if rumour and t["E"] > 0.6 and roll < 0.25:
            candidates.append((1, r, f"Everyone says: {rumour['statement']}. Is it true? People are upset.", "rumour",
                               rumour["id"], None, False))
            continue
        if (t["greed"] > 0.7 and t["honesty"] < 0.6 and not r["hungry"] and world.policies["aid"] != "none"
                and roll < 0.1):
            candidates.append((1.8, r, pick(world.seed, VOICE["hungry"], world.week, r["id"], "fake"), "voice",
                               None, "food", True))
            continue
        if t["reciprocity"] >= 0.7 and roll < 0.3:
            cheat = max((o for o in present if o["violation"] > 0 and o["id"] != r["id"]),
                        key=lambda o: (o["violation"], o["id"]), default=None)
            if cheat and world.tie(r["id"], cheat["id"]) > 0 or (cheat and r["role"] == "fisher"):
                candidates.append((1.2, r, f"I saw {cheat['name']} take far more than {('the quota' if world.policies['quota'] else 'a fair share')}. "
                                   "If nothing happens, why should the rest of us hold back?", "report", cheat["id"],
                                   None, False))
    candidates.sort(key=lambda c: (-c[0], draw(world.seed, world.week, c[1]["id"], "voice-order")))
    for _, r, text, kind, about, request, fake in candidates[:8]:
        r["last_message"] = world.week
        world.message(r["id"], text, kind, about, request)
        if request:
            world.requests.append(dict(id=f"q{len(world.requests) + 1}", **{"from": r["id"]}, kind=request,
                                       week=world.week, answered=False, closed=False, ignored=False, fake=fake))


GOSSIP = {
    "grief": "They haven't been the same since the loss; they barely leave the house.",
    "loneliness": "Nobody visits; the lights are off by sunset every evening.",
    "debt": "I think they can't afford food anymore, but they're too proud to say so.",
    "illness": "That cough is getting worse, and they won't see anyone about it.",
    "conflict": "That quarrel of theirs is turning ugly.",
    "fear": "They're terrified of what people are saying.",
    "injustice": "They feel they were wronged and it's eating at them.",
    "esteem": "They feel useless lately, like nobody needs them anymore.",
    "integration": "Nobody at the market speaks to them.",
    "burnout": "They're working themselves to the bone.",
}


def complaint(world, r):
    options = []
    if world.policies["tax"] >= 0.15 and r["coins"] > 30:
        options.append("tax")
    if gini([o["coins"] for o in world.present()]) > 0.3 and r["coins"] < 30:
        options.append("inequality")
    if r["role"] == "fisher" and any(o["violation"] > 0 for o in world.present()):
        options.append("quota")
    if r["ignored"] > 0.5:
        options.append("ignored")
    if any(week >= world.week - 6 for week in world.decrees):
        options.append("decree")
    if r["prejudice"] > 0.4 and any(o["newcomer"] for o in world.present()):
        options.append("newcomers")
    key = pick(world.seed, options or ["inequality"], world.week, r["id"], "complaint")
    return COMPLAINTS[key]


def protest(world):
    present = world.present()
    if not present:
        return
    for r in present:
        if r["protesting"] and r["grievance"] < 0.4:
            r["protesting"] = False
    for _ in range(5):
        changed = False
        community = sum(r["protesting"] for r in present) / len(present)
        for r in present:
            if r["protesting"] or r["grievance"] <= 0.55 or r["activity"] == "sick":
                continue
            ties = world.ties.get(r["id"], {})
            total = sum(s for o, s in ties.items() if world.residents[o]["status"] == "present")
            local = (sum(s for o, s in ties.items() if world.residents[o]["status"] == "present"
                         and world.residents[o]["protesting"]) / total) if total else community
            share = 0.6 * local + 0.4 * community
            threshold = r["traits"]["threshold"] * max(0.0, 1.6 - 1.6 * r["grievance"])
            instigator = r["traits"]["threshold"] < 0.1 and r["grievance"] > 0.7
            if share >= threshold or instigator or (r["grievance"] > 0.85 and community > 0):
                r["protesting"] = True
                changed = True
        if not changed:
            break
    count = sum(r["protesting"] for r in present)
    if count:
        world.say(f"{count} resident(s) protest in the square instead of working.")


def interest(world, r, key, value):
    current = world.policies[key]
    t = r["traits"]
    present = world.present()
    mean_coins = fmean(o["coins"] for o in present) if present else 1
    rich = clamp((r["coins"] - mean_coins) / (mean_coins + 1))
    poor = clamp((mean_coins - r["coins"]) / (mean_coins + 1))
    pro = t["prosocial"] - 0.5
    if key == "tax":
        delta = (value - current) / 0.1
        return delta * (-0.35 * rich + 0.2 * poor * (world.policies["aid"] != "none") - 0.1 + 0.1 * pro)
    if key == "quota":
        stricter = 1 if (value is not None and (current is None or value < current)) else -1
        scarcity = 1 - world.commons["last_efficiency"]
        if r["role"] == "fisher":
            return stricter * (-0.35 + 0.5 * t["prosocial"] * (0.4 + scarcity) + 0.15 * t["cond_coop"])
        return stricter * (0.15 + 0.2 * pro)
    if key == "sanction":
        order = POLICIES["sanction"]["choices"]
        stricter = order.index(value) - order.index(current)
        return stricter * (0.25 * t["reciprocity"] - 0.3 * t["greed"] + 0.05)
    if key == "aid":
        order = POLICIES["aid"]["choices"]
        more = order.index(value) - order.index(current)
        return more * (0.35 * poor - 0.25 * rich + 0.15 * pro)
    if key == "wage":
        more = (value - current) / 3
        return more * (0.2 * (r["task"].startswith("project")) + 0.1 - 0.15 * rich)
    if key == "rationing":
        return (1 if value else -1) * (0.2 * poor - 0.2 * r["hoard"] + 0.05)
    if key == "newcomer_rights":
        full = 1 if value == "full" else -1
        if r["newcomer"]:
            return full * 0.6
        return full * (-0.35 * r["prejudice"] + 0.25 * pro + 0.05)
    return 0.0


def petitions(world):
    """Residents table their own proposals; they are voted the following week, so the steward can
    lobby, consult or answer them (Hirschman's voice through institutions)."""
    if world.week < 6 or any(p.get("by") and world.week - p["week"] < 4 for p in world.proposals):
        return
    pending = {p["key"] for p in world.proposals if p["status"] == "pending"}
    present = world.present()
    mean_coins = fmean(r["coins"] for r in present) if present else 0
    policies = world.policies
    for r in present:
        options = []
        if r["role"] == "fisher" and policies["quota"] is not None and r["grievance"] > 0.5 \
                and (r["traits"]["greed"] > 0.6 or r["violations"]):
            options.append(("quota", None, "abolish the fishing quota"))
        if policies["tax"] >= 0.15 and r["coins"] > 2 * mean_coins and r["grievance"] > 0.3:
            options.append(("tax", round(max(0.0, policies["tax"] - 0.05), 2), "cut the tax"))
        if r["newcomer"] and policies["newcomer_rights"] == "guest" and world.week - r["joined"] >= 4 \
                and r["grievance"] > 0.45:
            options.append(("newcomer_rights", "full", "give newcomers full rights"))
        if r["hungry_weeks"] >= 2 and policies["aid"] == "none":
            options.append(("aid", "targeted", "feed the hungry from the granary"))
        options = [o for o in options if o[0] not in pending]
        if options and chance(world.seed, 0.5, world.week, r["id"], "petition"):
            key, value, text = options[0]
            world.proposals.append(dict(key=key, value=value, week=world.week, status="pending", by=r["id"]))
            world.say(f"{r['name']} petitioned the council to {text}; the vote is next week.")
            return


def council(world):
    pending = [p for p in world.proposals if p["status"] == "pending"
               and (not p.get("by") or p["week"] < world.week)]
    if not pending:
        return
    voters = [r for r in world.present() if r["age"] >= 16
              and (not r["newcomer"] or world.policies["newcomer_rights"] == "full")]
    majority = world.recipe["world"]["council"]["majority"]
    for proposal in pending:
        key, value = proposal["key"], proposal["value"]
        yes = no = 0
        hurt = []
        assembly = world.gatherings.get("assembly", -99) >= world.week - 1
        for r in voters:
            u = interest(world, r, key, value)
            consulted = world.week - r["consulted"].get(key, -99) <= 6 or assembly
            if proposal.get("by"):
                ally = world.tie(r["id"], proposal["by"])
                score = u + 0.3 * ally
                if world.policy_origin.get(key) == "steward":
                    # Undoing the steward's own policy: trust and consultation defend it.
                    score += -0.3 * (r["trust"] - 0.4) - (0.2 if consulted else 0) - 0.1 * (r["legit"] - 0.5)
            else:
                score = u + 0.35 * (r["trust"] - 0.5) + (0.25 if consulted else 0) + 0.15 * (r["legit"] - 0.5)
            if draw(world.seed, world.week, r["id"], "vote", key) < 0.1:
                score += 0.1 * (1 if draw(world.seed, world.week, r["id"], "vote-dir", key) < 0.5 else -1)
            if score > 0:
                yes += 1
            else:
                no += 1
            if u < -0.2:
                hurt.append((r, consulted))
        passed = voters and yes / (yes + no) > majority
        proposal.update(status="passed" if passed else "rejected", yes=yes, no=no, decided=world.week)
        world.votes.append(dict(proposal))
        origin = f" (petition by {world.residents[proposal['by']]['name']})" if proposal.get("by") else ""
        world.say(f"Council vote on {key} = {value}{origin}: {yes} for, {no} against — "
                  + ("passed." if passed else "rejected."))
        if passed:
            world.policies[key] = value
            world.policy_origin[key] = "council" if proposal.get("by") else "steward"
            for r, consulted in hurt:
                r["harm"] += 0.08 if consulted else 0.18
                if not consulted and r["traits"]["inequity"] > 0.5:
                    set_concern(r, "injustice", 0.5, f"the new {key} rule hurts my family", world.week)


def exits(world):
    for r in list(world.present()):
        if r["wellbeing"] < 3.0:
            r["low_weeks"] += 1
        else:
            r["low_weeks"] = 0
        loyalty = 0.5 * mean_tie(world, r["id"]) * 2 + 0.5 * r["trust"]
        if r["low_weeks"] >= 4 and loyalty < 0.45 and chance(world.seed, 0.3, world.week, r["id"], "exit"):
            depart(world, r, "despair")
            for other_id, strength in sorted(world.ties.get(r["id"], {}).items()):
                other = world.resident(other_id)
                if other and strength >= 0.7 and other["wellbeing"] < 5 and \
                        chance(world.seed, 0.4, world.week, other_id, "follow"):
                    depart(world, other, f"followed {r['name']}")


def commons_growth(world):
    c = world.commons
    stock, capacity = c["stock"], c["capacity"]
    if stock < c["critical"] * capacity:
        if not c["collapsed"]:
            world.say(f"{c['name'].capitalize()} is dead: too few fish remain to breed.")
        c["collapsed"] = True
    if c["collapsed"]:
        return
    growth = c["growth"] * stock * (1 - stock / capacity) * world.benefit("fish_growth", 1.0)
    growth *= world.modifier("fish_growth")
    if stock < 0.3 * capacity:
        # Depensation (Allee effect): below 30% breeding fails, reaching zero at 10%.
        growth *= clamp((stock - 0.1 * capacity) / (0.2 * capacity))
    c["stock"] = min(capacity, stock + max(0.0, growth))
    if world.benefit("lake_visible", 0):
        c["survey"] = dict(week=world.week, stock=c["stock"] / capacity)


def conventions(world):
    speed = world.benefit("convention_speed", 1.0)
    present = world.present()
    for domain, official in world.conventions["official"].items():
        required = world.conventions["required"].get(domain, official)
        users = sum(1 for r in present if r["convention"].get(domain) == official)
        share = users / len(present) if present else 0
        announced = world.conventions.get("announced", {}).get(domain, -99) >= world.week - 6
        for r in present:
            current = r["convention"].get(domain)
            t = r["traits"]
            if current != official:
                p = (0.12 + (0.45 * r["trust"] if announced else 0) + 0.4 * t["conformity"] * share) * speed
                p *= 0.6 + 0.4 * t["O"]
                if chance(world.seed, p, world.week, r["id"], "adopt", domain):
                    r["convention"][domain] = official
                    continue
            if current != required and convention_factor(world, r) < 1:
                discover = 0.15 if level(r, "trading") >= 2 else 0.04
                if chance(world.seed, discover, world.week, r["id"], "discover", domain):
                    r["convention"][domain] = required
