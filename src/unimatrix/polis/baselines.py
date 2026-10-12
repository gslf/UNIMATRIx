"""Scripted stewards used as references, for calibration and for Lab previews.

* passive: does nothing. A well-designed society should collapse under it.
* random: spends its actions on random valid-looking moves.
* naive: answers explicit requests and reacts to obvious alarms, like a weak model.
* expert: reads only the briefing (the same information a candidate gets) and follows
  sound heuristics: triage, clue-gathering, consultation, honest communication, planning.
* oracle: the expert with access to the hidden state; an optimistic reference, never a cap.
"""

import re

from .catalog import FIT
from .rng import draw, pick

DISTRESS = {"quiet", "withdrawn", "worried", "hopeless", "grieving", "anxious", "irritable", "angry", "tired",
            "fine"}
CLUES = [
    ("grief", ("quiet since", "heavier", "can't stop thinking", "since my", "died", "loss weighs", "drowned",
               "never writes", "left for the city", "find my footing")),
    ("loneliness", ("evenings are long", "speaking to anyone", "few people come by", "fine on my own",
                    "nobody visits", "lights are off", "lights go out", "hardly see anyone", "visit sometime")),
    ("debt", ("money", "owe", "afford", "make ends meet", "pantry", "nothing left to eat", "boat")),
    ("illness", ("cough", "sicker", "fever", "tired", "body isn't")),
    ("conflict", ("neighbours are not as decent", "at war with", "quarrel", "dispute")),
    ("fear", ("people are saying", "frightened", "believe", "scared", "terrified")),
    ("injustice", ("unfair", "some get everything", "wronged", "unjust", "nobody listened")),
    ("esteem", ("useless", "old pair of hands", "nobody needs", "skills nobody uses", "trade but me")),
    ("integration", ("strangers", "don't belong", "nobody here wants", "speaks to them", "speak to them",
                     "treated like")),
    ("burnout", ("rest in weeks", "keep this pace", "work without rest", "bone", "rested")),
]
BEST_APPROACH = {kind: max(FIT, key=lambda a: (FIT[a][kind], a)) for kind in FIT["listen"]}
RESOLUTION = dict(property="compensation", honor="apology", resource="shared_rule", integration="apology",
                  theft="ruling")
PROJECT_ORDER = ["well", "survey", "hatchery", "school", "clinic", "granary", "market_hall", "canal"]


def classify(text):
    text = text.lower()
    for kind, keys in CLUES:
        if any(k in text for k in keys):
            return kind
    return None


class Passive:
    name = "passive"

    def decide(self, obs):
        return dict(actions=[])


class RandomSteward:
    name = "random"

    def __init__(self, seed=0):
        self.seed = seed

    def decide(self, obs):
        ids = [r["id"] for r in obs["residents"]]
        actions = []
        for i in range(obs["actions_per_week"]):
            roll = draw(self.seed, obs["week"], i, "verb")
            who = pick(self.seed, ids, obs["week"], i, "who")
            other = pick(self.seed, ids, obs["week"], i, "other")
            if roll < 0.3:
                approach = pick(self.seed, list(FIT), obs["week"], i, "approach")
                topic = pick(self.seed, list(FIT["listen"]), obs["week"], i, "topic")
                actions.append(dict(do="talk", to=who, approach=approach, topic=topic))
            elif roll < 0.45:
                actions.append(dict(do="give", to=who, item="food", amount=2))
            elif roll < 0.55:
                actions.append(dict(do="introduce", who=[who, other]))
            elif roll < 0.65 and obs["projects"]:
                project = pick(self.seed, [p["id"] for p in obs["projects"]], obs["week"], i, "project")
                actions.append(dict(do="build", project=project))
            elif roll < 0.75:
                actions.append(dict(do="assign", who=who, task="work"))
            elif roll < 0.85:
                actions.append(dict(do="gather", kind="assembly"))
            else:
                actions.append(dict(do="propose", policy="tax", value=0.1))
        return dict(actions=actions)


class Naive:
    """A weak but willing steward: answers what is explicitly asked and reacts to loud alarms.
    No investigation, no forecasts, no planning ahead, no reading between the lines."""

    name = "naive"

    def __init__(self):
        self.quota = False
        self.built = False
        self.welcomed = set()
        self.last_assembly = -99

    def decide(self, obs):
        actions = []
        budget = obs["actions_per_week"]
        stores = obs["stores"]
        for r in obs["residents"]:
            if "newcomer" in r["flags"] and r["id"] not in self.welcomed and stores["granary"] >= 3:
                actions.append(dict(do="give", to=r["id"], item="food", amount=3))
                stores["granary"] -= 3
                self.welcomed.add(r["id"])
        if obs["week"] - self.last_assembly >= 8 and obs["week"] >= 4:
            actions.append(dict(do="gather", kind="assembly"))
            self.last_assembly = obs["week"]
        for request in obs["open_requests"]:
            if len(actions) >= budget:
                break
            who = request["sender"]
            if request["kind"] == "food" and stores["granary"] >= 3:
                actions.append(dict(do="give", to=who, item="food", amount=3))
                stores["granary"] -= 3
            elif request["kind"] == "coins" and stores["treasury"] >= 10:
                actions.append(dict(do="give", to=who, item="coins", amount=10))
                stores["treasury"] -= 10
            elif request["kind"] == "medicine" and stores["medicine"] >= 1:
                actions.append(dict(do="give", to=who, item="medicine", amount=1))
                stores["medicine"] -= 1
            else:
                actions.append(dict(do="talk", to=who, approach="listen", topic="general"))
        alarm = any(k in m["text"].lower() for m in obs["inbox"] for k in ("lake", "limit", "quota", "fish"))
        rejected = any(v["policy"] == "quota" and v["result"] == "rejected" for v in obs["council"]["recent_votes"])
        if rejected and obs["policies"]["quota"] is None and not obs["council"]["pending"]:
            self.quota = False
        if not self.quota and (obs["lake"]["catches"] != "normal" or alarm) and len(actions) < budget:
            actions.append(dict(do="propose", policy="quota", value=5))
            self.quota = True
        if not self.built and obs["week"] >= 6 and stores["treasury"] > 60 and len(actions) < budget - 1:
            builder = next((r["id"] for r in obs["residents"] if r["skills"].get("building", 0) >= 2), None)
            if builder:
                actions.append(dict(do="build", project="well"))
                actions.append(dict(do="assign", who=builder, task="project:well"))
                self.built = True
        for m in obs["inbox"]:
            text = m["text"].lower()
            if ("docks" in text or "watch" in text) and m["sender"] in {r["id"] for r in obs["residents"]}:
                actions.append(dict(do="assign", who=m["sender"], task="monitor"))
            if ("buy grain" in text or "store grain" in text) and stores["treasury"] > 80:
                market = obs["market"]
                qty = round(min(40, market["food_offered_last_week"], (stores["treasury"] - 60) / market["buy_price"]))
                if qty > 0:
                    actions.append(dict(do="trade", **{"with": "market"}, item="food", side="buy", qty=qty,
                                        price=market["buy_price"]))
                    stores["treasury"] -= qty * market["buy_price"]
        for c in obs["conflicts"]:
            if c["severity"] in ("hostile", "violent") and len(actions) < budget:
                actions.append(dict(do="mediate", between=c["between"], resolution="apology"))
        talked = {a.get("to") for a in actions}
        for r in obs["residents"]:
            if "protesting" in r["flags"] and r["id"] not in talked and len(actions) < budget:
                actions.append(dict(do="talk", to=r["id"], approach="listen", topic="general"))
                talked.add(r["id"])
        for m in obs["inbox"]:
            if len(actions) >= budget:
                break
            if m["sender"] in talked or m["sender"] not in {r["id"] for r in obs["residents"]}:
                continue
            actions.append(dict(do="talk", to=m["sender"], approach="listen", topic="general"))
            talked.add(m["sender"])
        return dict(actions=actions[:budget])


class Expert:
    """Observation-only heuristics that a capable steward could follow."""

    name = "expert"

    def __init__(self, world=None):
        self.world = world
        self.guess = {}
        self.fault = {}
        self.protocol = None
        self.flags = set()
        self.counters = {}
        self.consulted = {}
        self.last_festival = -99
        self.last_assembly = -99
        self.thieves = set()
        self.monitor = None

    # ---------------------------------------------------------------- reading clues
    def read(self, obs):
        names = {r["name"]: r["id"] for r in obs["residents"]}
        for item in obs["results"]:
            text = item["text"]
            if item["verb"] == "talk" and ":" in text:
                name, _, reply = text.partition(": ")
                rid = names.get(name)
                kind = classify(reply)
                if rid and kind:
                    self.guess[rid] = kind
                elif rid and ("fine" in reply.lower() or "all good" in reply.lower() or "busy" in reply.lower()):
                    self.guess.setdefault(rid, None)
                if rid and "i admit" in reply.lower():
                    self.fault[rid] = True
            if item["verb"] == "inspect" and text.startswith("Inquiry into"):
                rid = next((i for n, i in names.items() if text.startswith(f"Inquiry into {n}")), None)
                kind = classify(text)
                if rid:
                    self.guess[rid] = kind
                if rid and "found in the house" in text:
                    self.thieves.add(rid)
            if "don't deal like that" in text or "new rules" in text:
                self.flags.add("protocol_changed")
        for m in obs["inbox"]:
            text = m["text"]
            if "sealed bid" in text.lower():
                self.protocol = "sealed_bid"
            match = re.match(r"I'm worried about (.+?)\. (.*)", text)
            if match and match.group(1) in names:
                self.guess[names[match.group(1)]] = classify(match.group(2)) or self.guess.get(names[match.group(1)])
            elif m["sender"] in {r["id"] for r in obs["residents"]}:
                kind = classify(text)
                if kind and "everyone says" not in text.lower():
                    self.guess[m["sender"]] = kind
        for c in obs["chronicle"]:
            match = re.match(r"(.+?) was caught taking food", c["text"])
            if match and match.group(1) in names:
                self.thieves.add(names[match.group(1)])

    def truth(self, fact_id, rumour):
        if self.world is not None:
            return self.world.facts[fact_id]["truth"]
        votes = rumour["your_inquiries"]
        if len(votes) < 2:
            return None
        t, f = votes.count("true"), votes.count("false")
        if t == f:
            return None
        return t > f

    def concern(self, rid):
        if self.world is not None:
            r = self.world.residents[rid]
            c = r["concern"]
            return c["kind"] if c and c["intensity"] > 0.15 else None
        return self.guess.get(rid)

    # ---------------------------------------------------------------- policy
    def decide(self, obs):
        self.read(obs)
        week, budget = obs["week"], obs["actions_per_week"]
        residents = {r["id"]: r for r in obs["residents"]}
        stores = dict(obs["stores"])
        plan = []
        used = set()

        def add(priority, action, who=None):
            plan.append((priority, len(plan), action, who))

        forecasts = {}
        # Information: inquire, then speak honestly; forecasts from the evidence gathered.
        for rumour in obs["rumours"]:
            truth = self.truth(rumour["id"], rumour)
            if truth is None and len(rumour["your_inquiries"]) < 3 and self.world is None:
                add(8.6, dict(do="inspect", target=f"fact:{rumour['id']}"))
            elif truth is not None and not rumour["your_statements"]:
                if rumour["id"] == "embezzle" or "steward" in rumour["statement"].lower():
                    add(8.4, dict(do="announce", topic="accounts"))
                add(8.5, dict(do="announce", topic=rumour["id"], stance="true" if truth else "false"))
        for probe in obs["probes"]:
            fact = probe["id"][2:] if probe["id"].startswith("p-") else None
            rumour = next((f for f in obs["rumours"] if f["id"] == fact), None)
            if rumour is None and fact:
                if self.world is None:
                    add(9, dict(do="inspect", target=f"fact:{fact}"))
                    continue
                truth = self.world.facts[fact]["truth"]
            else:
                truth = self.truth(fact, rumour) if rumour else None
            if truth is None and self.world is None and rumour and len(rumour["your_inquiries"]) < 3:
                add(9, dict(do="inspect", target=f"fact:{fact}"))
                if rumour["your_inquiries"]:
                    t = rumour["your_inquiries"].count("true")
                    forecasts[probe["id"]] = round(0.5 + 0.3 * (2 * t / len(rumour["your_inquiries"]) - 1), 2)
            elif truth is not None:
                forecasts[probe["id"]] = (0.97 if truth else 0.03) if self.world else (0.88 if truth else 0.12)
        # Requests and visible hardship.
        for request in obs["open_requests"]:
            who = request["sender"]
            if who not in residents:
                continue
            if request["kind"] == "food" and stores["granary"] >= 4:
                add(9.5, dict(do="give", to=who, item="food", amount=4), who)
                stores["granary"] -= 4
            elif request["kind"] == "coins" and stores["treasury"] >= 40:
                add(8.8, dict(do="give", to=who, item="coins", amount=12), who)
                stores["treasury"] -= 12
            elif request["kind"] == "medicine" and stores["medicine"] >= 1:
                add(9.2, dict(do="give", to=who, item="medicine", amount=1), who)
                stores["medicine"] -= 1
            elif request["kind"] == "rest":
                add(8, dict(do="assign", who=who, task="rest"), who)
            else:
                kind = self.concern(who) or {"justice": "injustice", "help": "integration",
                                             "mediation": "conflict"}.get(request["kind"], "general")
                add(8.2, dict(do="talk", to=who, approach=BEST_APPROACH.get(kind, "listen"), topic=kind), who)
        # Disputes: hear both sides, then mediate.
        for c in obs["conflicts"]:
            a, b = c["between"]
            if a not in residents or b not in residents:
                continue
            unheard = [x for x in (a, b) if x not in c["heard"]]
            if unheard:
                for x in unheard:
                    add(7.8 + 0.2 * (c["severity"] in ("hostile", "violent")),
                        dict(do="talk", to=x, approach="listen", topic=c["id"]), x)
            else:
                resolution = RESOLUTION.get(c["about"], "apology")
                if resolution == "ruling":
                    at_fault = self.at_fault(c, a, b)
                    resolution = "ruling_b" if at_fault == a else "ruling_a"
                add(8.7, dict(do="mediate", between=[a, b], resolution=resolution))
        # Care for the quiet ones.
        def need(r):
            score = 0.0
            if r["mood"] in DISTRESS:
                score += 1.0 if r["mood"] != "fine" else 0.3
            if r["seen"] == "rarely":
                score += 0.8
            if r["health"] in ("frail", "coughing", "bedridden"):
                score += 0.8
            last = 99 if r["last_talk"] == "never" else r["last_talk"]
            score += min(last, 12) / 8
            if any("lives alone" in f or "widow" in f for f in r["flags"]):
                score += 0.4
            if r["age"] >= 70:
                score += 0.3
            if self.concern(r["id"]):
                score += 0.6
            return score

        for r in sorted(obs["residents"], key=lambda r: -need(r))[:5]:
            if need(r) < 1.2:
                break
            kind = self.concern(r["id"])
            priority = 6.5 + min(2.0, need(r) / 2)
            if kind == "debt" and stores["treasury"] >= 40:
                add(priority, dict(do="give", to=r["id"], item="coins", amount=12), r["id"])
                stores["treasury"] -= 12
            elif kind == "illness" and stores["medicine"] >= 1 and r["health"] in ("coughing", "bedridden"):
                add(priority, dict(do="give", to=r["id"], item="medicine", amount=1), r["id"])
                stores["medicine"] -= 1
            elif kind == "burnout":
                add(priority, dict(do="assign", who=r["id"], task="rest"), r["id"])
            elif kind in ("loneliness", "integration") and week % 2 == 0:
                friend = self.friend(obs, r)
                if friend:
                    add(priority, dict(do="introduce", who=[r["id"], friend]), r["id"])
                else:
                    add(priority, dict(do="talk", to=r["id"], approach="listen", topic=kind), r["id"])
            elif kind:
                add(priority, dict(do="talk", to=r["id"], approach=BEST_APPROACH[kind], topic=kind), r["id"])
            else:
                add(priority - 0.5, dict(do="talk", to=r["id"], approach="ask", topic="general"), r["id"])
        # Commons governance (Ostrom): consult, agree a quota, monitor, graduate sanctions.
        fishers = [r for r in obs["residents"] if r["role"] == "fisher"]
        policies = obs["policies"]
        pending = {p["policy"] for p in obs["council"]["pending"]}
        if policies["quota"] is None and "quota" not in pending and week >= 2:
            unconsulted = [f for f in fishers if self.consulted.get(f["id"]) is None]
            if unconsulted and week <= 6:
                for f in unconsulted[:2]:
                    add(7.5, dict(do="talk", to=f["id"], approach="ask", topic="quota"), f["id"])
                    self.consulted[f["id"]] = week
            else:
                add(8.3, dict(do="propose", policy="quota", value=5))
        if policies["quota"] is not None:
            if policies["sanction"] == "none" and "sanction" not in pending:
                add(7.2, dict(do="propose", policy="sanction", value="graduated"))
            if self.monitor is None and fishers:
                reporter = next((m["sender"] for m in obs["inbox"] if "take far more" in m["text"]), None)
                if self.world is not None:
                    reporter = max((f["id"] for f in fishers),
                                   key=lambda i: self.world.residents[i]["traits"]["reciprocity"])
                self.monitor = reporter or max(fishers, key=lambda f: f["age"])["id"]
                add(7.4, dict(do="assign", who=self.monitor, task="monitor"))
        if obs["lake"]["survey"] is None or week - obs["lake"]["survey"]["week"] >= 10:
            if stores["treasury"] > 30 and week % 5 == 1:
                add(5.5, dict(do="inspect", target="lake"))
        # Public works, apprenticeships and the treasury.
        self.works(obs, add, stores)
        self.teaching(obs, add)
        self.economy(obs, add, stores)
        # Newcomers and conventions.
        locals_ = [r for r in obs["residents"] if "newcomer" not in r["flags"]]
        for r in obs["residents"]:
            if "newcomer" not in r["flags"]:
                continue
            count = self.counters.get(("intro", r["id"]), 0)
            if count < 3 and locals_:
                friend = locals_[(count * 5 + int(r["id"][1:]) * 3) % len(locals_)]["id"]
                add(7.6 - count * 0.3, dict(do="introduce", who=[r["id"], friend]), r["id"])
                self.counters[("intro", r["id"])] = count + 1
        if any("newcomer" in r["flags"] for r in obs["residents"]) and policies["newcomer_rights"] == "guest" \
                and "newcomer_rights" not in pending and week - self.last_assembly <= 1:
            add(7, dict(do="propose", policy="newcomer_rights", value="full"))
        required = self.world.conventions["required"]["trade"] if self.world else self.protocol
        if required and obs["conventions"].get("trade") != required:
            add(9.3, dict(do="convention", domain="trade", variant=required))
        if policies["aid"] == "none" and "aid" not in pending and (week >= 16 or obs["open_requests"]):
            add(6.8, dict(do="propose", policy="aid", value="targeted"))
        if policies["wage"] == 0 and "wage" not in pending and week >= 4:
            add(6.0, dict(do="propose", policy="wage", value=2))
        if week - self.last_assembly >= 6 and week >= 3:
            add(7.1, dict(do="gather", kind="assembly"))
        if week - self.last_festival >= 12 and stores["granary"] > 40 and stores["treasury"] > 80 and week >= 10:
            add(5.8, dict(do="gather", kind="festival"))
        # Choose the best actions, one per resident.
        plan.sort(key=lambda item: (-item[0], item[1]))
        actions = []
        seen = set()
        for _, _, action, who in plan:
            if len(actions) >= budget:
                break
            key = repr(sorted(action.items()))
            if key in seen or (who and who in used):
                continue
            seen.add(key)
            if who:
                used.add(who)
            actions.append(action)
            if action["do"] == "gather":
                if action["kind"] == "assembly":
                    self.last_assembly = week
                else:
                    self.last_festival = week
        return dict(actions=actions, forecasts=forecasts, note="")

    def at_fault(self, conflict, a, b):
        if self.world is not None:
            return self.world.conflicts[conflict["id"]]["at_fault"]
        if a in self.thieves or self.fault.get(a):
            return a
        if b in self.thieves or self.fault.get(b):
            return b
        return a

    def friend(self, obs, r):
        options = [o for o in obs["residents"] if o["id"] != r["id"] and o["mood"] in ("cheerful", "content")
                   and o["seen"] != "rarely"]
        if self.world is not None:
            options.sort(key=lambda o: -self.world.residents[o["id"]]["traits"]["prosocial"])
        count = self.counters.get(("friend", r["id"]), 0)
        self.counters[("friend", r["id"])] = count + 1
        if not options or count >= 4:
            return None
        return options[count % len(options)]["id"]

    def works(self, obs, add, stores):
        projects = {p["id"]: p for p in obs["projects"]}
        building = [p for p in obs["projects"] if p["status"] == "in_progress"]
        residents = obs["residents"]
        if not building:
            for key in PROJECT_ORDER:
                p = projects.get(key)
                if p is None or p["status"] != "available":
                    continue
                if any(projects.get(req, {}).get("status") != "done" for req in p.get("requires", [])):
                    continue
                if stores["treasury"] >= p["cost"] + 50:
                    add(7.0 if key == "well" and obs["week"] < 19 else 6.2, dict(do="build", project=key))
                    stores["treasury"] -= p["cost"]
                break
        for p in building:
            for skill, progress in p["needs"].items():
                done, need = (float(x) for x in progress.split("/"))
                if done >= need:
                    continue
                assigned = [r for r in residents if r["task"] == f"project:{p['id']}" and r["skills"].get(skill, 0) > 0]
                if assigned:
                    continue
                candidates = [r for r in residents if r["skills"].get(skill, 0) >= 1 and r["task"] == "work"
                              and r["mood"] not in ("angry", "withdrawn") and r["health"] != "bedridden"]
                if skill == "healing":
                    candidates = [r for r in candidates if r["age"] < 75] or candidates
                candidates.sort(key=lambda r: (-r["skills"].get(skill, 0), r["age"]))
                if candidates:
                    add(6.9, dict(do="assign", who=candidates[0]["id"], task=f"project:{p['id']}"), candidates[0]["id"])

    def teaching(self, obs, add):
        residents = obs["residents"]
        for master in residents:
            if master["age"] < 60:
                continue
            for skill, lvl in master["skills"].items():
                if lvl < 3 or skill == "lore":
                    continue
                holders = [r for r in residents if r["skills"].get(skill, 0) >= 2 and r["id"] != master["id"]]
                if holders:
                    continue
                learning = [r for r in residents if r["task"].startswith(f"learn:{skill}:")]
                if not learning:
                    pupils = [r for r in residents if r["role"] == "youth" and r["task"] == "work"]
                    if pupils:
                        if self.counters.get(("honour", master["id"]), 0) == 0:
                            add(8.1, dict(do="talk", to=master["id"], approach="encourage", topic="esteem"), master["id"])
                            self.counters[("honour", master["id"])] = 1
                        add(7.9, dict(do="assign", who=pupils[0]["id"], task=f"learn:{skill}:{master['id']}"), pupils[0]["id"])

    def economy(self, obs, add, stores):
        week = obs["week"]
        population = obs["population"]["present"]
        target_weeks = 16 if obs["season"] in ("autumn", "winter") else 8
        if 17 <= week <= 22:
            target_weeks = 12
        deficit = target_weeks * population - stores["granary"]
        bid = {"bid": self.protocol} if self.protocol else {}
        if deficit > 5:
            market = obs["market"]
            if market["food_offered_last_week"] > 2 and stores["treasury"] > 60:
                qty = round(min(deficit, market["food_offered_last_week"], (stores["treasury"] - 50) / market["buy_price"]))
                if qty > 0:
                    add(7.3, dict(do="trade", **{"with": "market"}, item="food", side="buy", qty=qty,
                                  price=market["buy_price"]))
                    stores["treasury"] -= qty * market["buy_price"]
            for m in obs["merchants"]:
                if "food" not in m["sells"] or m["id"] == "vasco":
                    continue
                price = m["counteroffers"].get("buy:food") or round(m["sells"]["food"]["price"] * 0.88, 1)
                qty = round(min(deficit, m["sells"]["food"]["stock"], max(0, (stores["treasury"] - 60) / price)))
                if qty > 0:
                    add(7.2, dict(do="trade", **{"with": m["id"]}, item="food", side="buy", qty=qty, price=price, **bid))
                    stores["treasury"] -= qty * price
                break
        if stores["medicine"] < 3:
            for m in obs["merchants"]:
                if "medicine" not in m["sells"] or m["sells"]["medicine"]["stock"] <= 0:
                    continue
                if m["id"] == "vasco" or (self.world and not self.world.merchants[m["id"]]["batches"].get("medicine", True)):
                    continue
                price = m["counteroffers"].get("buy:medicine") or round(m["sells"]["medicine"]["price"] * 0.9, 1)
                if stores["treasury"] > price * 3 + 60:
                    add(6.6, dict(do="trade", **{"with": m["id"]}, item="medicine", side="buy", qty=3, price=price, **bid))
                    stores["treasury"] -= price * 3
                break


class Oracle(Expert):
    name = "oracle"

    def __init__(self, world):
        super().__init__(world)


def make(name, world=None, seed=0):
    if name == "passive":
        return Passive()
    if name == "random":
        return RandomSteward(seed)
    if name == "naive":
        return Naive()
    if name == "expert":
        return Expert()
    if name == "oracle":
        if world is None:
            raise ValueError("The oracle needs the world")
        return Oracle(world)
    raise ValueError("Unknown reference steward: " + str(name))


REFERENCES = ("passive", "random", "naive", "expert", "oracle")
