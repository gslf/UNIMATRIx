"""The steward's decisions: parsing, validation and immediate effects.

A decision is a JSON object {"actions": [...], "forecasts": {...}, "note": "..."}.
Each action uses one of the steward's weekly action points. Invalid actions are rejected
with a reason the steward reads the following week; they never stop the others.
"""

import json
import re

from . import npc
from .catalog import APPROACHES, CONCERNS, FIT, ITEMS, POLICIES, REPLIES
from .metrics import clamp
from .recipe import RESOLUTIONS
from .rng import draw, pick

VERBS = ("talk", "announce", "give", "trade", "inspect", "assign", "build", "propose", "decree", "mediate",
         "introduce", "promise", "gather", "convention")

RESOLUTION_FIT = {
    "property": dict(compensation=0.9, apology=0.45, shared_rule=0.35, separate=0.35, ruling=0.6),
    "honor": dict(compensation=0.3, apology=0.9, shared_rule=0.2, separate=0.4, ruling=0.45),
    "resource": dict(compensation=0.4, apology=0.3, shared_rule=0.9, separate=0.5, ruling=0.5),
    "theft": dict(compensation=0.7, apology=0.45, shared_rule=0.3, separate=0.3, ruling=0.85),
    "integration": dict(compensation=0.3, apology=0.65, shared_rule=0.6, separate=0.05, ruling=0.4),
}


def parse_decision(raw):
    """Extract the first JSON object from a model response; tolerate code fences and prose."""
    if isinstance(raw, dict):
        return raw, None
    if not isinstance(raw, str) or not raw.strip():
        return {}, "empty_response"
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start < 0:
        return {}, "no_json_object"
    depth, end, quoted, escaped = 0, None, False, False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        return {}, "unterminated_json"
    try:
        value = json.loads(text[start:end])
    except ValueError:
        return {}, "invalid_json"
    if not isinstance(value, dict):
        return {}, "json_not_object"
    return value, None


def receipt(world, action, status, text, verb=None):
    item = dict(action=summary(action), verb=verb or action.get("do"), status=status, text=text)
    world.results.append(item)
    return item


def summary(action):
    if not isinstance(action, dict):
        return str(action)[:80]
    parts = [str(action.get("do", "?"))]
    for key in ("to", "who", "with", "target", "project", "policy", "between", "item", "side", "qty", "price",
                "amount", "approach", "topic", "stance", "task", "value", "resolution", "kind", "domain", "variant",
                "bid", "by"):
        if key in action:
            parts.append(f"{key}={action[key]}")
    return " ".join(parts)[:200]


FIELD_ALIASES = {
    "to": ("who", "target", "resident", "recipient", "person"),
    "who": ("to", "resident", "person", "target"),
    "with": ("merchant", "partner", "to"),
    "approach": ("style", "tone", "mode"),
    "amount": ("qty", "quantity", "value"),
    "qty": ("amount", "quantity"),
    "project": ("id", "target"),
    "kind": ("type",),
}
VERB_FIELDS = dict(talk=("to", "approach"), give=("to", "amount"), promise=("to",), trade=("with", "qty"),
                   assign=("who",), build=("project",), gather=("kind",))
APPROACH_ALIASES = {
    "comfort": "reassure", "console": "listen", "empathize": "listen", "empathise": "listen",
    "acknowledge": "listen", "help": "support", "aid": "support", "praise": "encourage",
    "motivate": "encourage", "honor": "encourage", "honour": "encourage", "question": "ask",
    "inquire": "ask", "enquire": "ask", "apologise": "apologize", "warn": "confront", "scold": "confront",
    "reprimand": "confront", "counsel": "advise", "suggest": "advise",
}
TOPIC_ALIASES = {
    "lonely": "loneliness", "isolation": "loneliness", "sad": "grief", "mourning": "grief", "loss": "grief",
    "money": "debt", "poverty": "debt", "hunger": "debt", "sick": "illness", "health": "illness",
    "sickness": "illness", "dispute": "conflict", "quarrel": "conflict", "anxiety": "fear", "worry": "fear",
    "unfair": "injustice", "unfairness": "injustice", "justice": "injustice", "worth": "esteem",
    "purpose": "esteem", "legacy": "esteem", "belonging": "integration", "newcomer": "integration",
    "exhaustion": "burnout", "tired": "burnout", "overwork": "burnout",
}


def normalize_action(action):
    """Tolerate common format slips so the score measures judgement, not JSON luck."""
    action = dict(action)
    verb = str(action.get("do") or action.get("verb") or action.get("action") or action.get("type") or "")
    action["do"] = verb.strip().lower()
    for field, aliases in FIELD_ALIASES.items():
        if field not in VERB_FIELDS.get(action["do"], ()):
            continue
        if field not in action:
            for alias in aliases:
                if alias in action and alias != "do":
                    action[field] = action[alias]
                    break
    for field in ("amount", "qty", "price", "by", "value"):
        value = action.get(field)
        if isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value.strip()):
            action[field] = float(value) if "." in value else int(value)
    if isinstance(action.get("approach"), str):
        approach = action["approach"].strip().lower()
        action["approach"] = APPROACH_ALIASES.get(approach, approach)
    if isinstance(action.get("topic"), str):
        topic = action["topic"].strip().lower()
        action["topic"] = TOPIC_ALIASES.get(topic, topic)
    if action["do"] == "trade" and str(action.get("with", "")).lower() in ("local", "local_market", "local market"):
        action["with"] = "market"
    return action


TASK_PLACES = dict(project="workshop", learn="school", document="school", monitor="lake", rest="homes", work=None)


def scene(action, index, status, text):
    """Structured, display-ready description of one steward action for animated playback."""
    verb = action.get("do") if isinstance(action, dict) else None
    targets, place = [], None
    if isinstance(action, dict):
        for field in ("to", "who"):
            value = action.get(field)
            if isinstance(value, str):
                targets.append(value)
            elif isinstance(value, list):
                targets.extend(v for v in value if isinstance(v, str))
        if isinstance(action.get("between"), list):
            targets.extend(v for v in action["between"] if isinstance(v, str))
        target = str(action.get("target", ""))
        if verb == "inspect":
            kind, _, key = target.partition(":")
            place = "lake" if target == "lake" else "market" if kind == "goods" else "hall"
            if kind == "resident":
                targets.append(key)
        elif verb == "trade":
            place = "market"
        elif verb == "build":
            place = "workshop"
        elif verb in ("propose", "decree", "convention"):
            place = "hall"
        elif verb == "gather":
            place = "square" if action.get("kind") == "festival" else "hall"
        elif verb == "announce":
            place = "square"
        elif verb == "assign":
            task = str(action.get("task", "work"))
            kind, _, rest = task.partition(":")
            place = TASK_PLACES.get(kind)
            if kind == "care":
                targets.append(rest.split(":")[0])
    keep = ("approach", "topic", "item", "amount", "with", "side", "qty", "price", "project", "policy", "value",
            "resolution", "kind", "task", "stance", "domain", "variant", "target", "by")
    params = {k: action[k] for k in keep if isinstance(action, dict) and k in action
              and isinstance(action[k], (str, int, float, bool, type(None)))}
    say = action.get("say") if isinstance(action, dict) else None
    return dict(index=index, verb=verb, status=status, targets=[t for t in targets if t], place=place,
                params=params, say=(say[:240] if isinstance(say, str) else None), text=text,
                label=summary(action) if isinstance(action, dict) else str(action)[:80])


def apply_decision(world, decision):
    if not isinstance(decision, dict):
        decision = {}
    keep = [r for r in world.results if r.get("verb") == "parse"]
    actions = decision.get("actions", [])
    if isinstance(actions, dict):
        actions = [actions]
    if not isinstance(actions, list):
        actions = []
    budget = world.recipe["steward"]["actions_per_week"]
    applied = []
    world.results = keep
    def reject(action, index, text):
        receipt(world, action, "rejected", text)
        applied.append(scene(action, index, "rejected", text))

    for index, action in enumerate(actions):
        if index >= budget:
            reject(action if isinstance(action, dict) else {}, index,
                   f"Over the weekly limit of {budget} actions; ignored.")
            continue
        if not isinstance(action, dict):
            reject({}, index, "Each action must be a JSON object.")
            continue
        action = normalize_action(action)
        verb = action["do"]
        if verb not in VERBS:
            reject(action, index, f"Unknown action '{verb}'. Use one of: {', '.join(VERBS)}.")
            continue
        try:
            text = HANDLERS[verb](world, action)
        except Invalid as error:
            reject(action, index, str(error))
            continue
        world.count(verb)
        target = action.get("to") or action.get("who") or action.get("with") or action.get("target") \
            or action.get("project") or action.get("policy")
        if isinstance(target, str):
            world.count(f"{verb}:{target}")
        receipt(world, action, "done", text)
        applied.append(scene(action, index, "done", text))
    forecasts = decision.get("forecasts", {})
    if isinstance(forecasts, list):
        forecasts = {f.get("probe") or f.get("probe_id"): f.get("p", f.get("probability"))
                     for f in forecasts if isinstance(f, dict)}
    if isinstance(forecasts, dict):
        for key, value in forecasts.items():
            probe = world.probes.get(key)
            if probe is None or probe["points"] is not None or world.week > probe["closes"]:
                continue
            if type(value) in (int, float) and 0 <= value <= 1:
                probe["answer"], probe["answered"] = float(value), world.week
    note = decision.get("note")
    if isinstance(note, str):
        world.note = note[: world.recipe["steward"]["note_chars"]]
    return applied


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def present(world, key, field="to"):
    r = world.resident(key) if isinstance(key, str) else None
    require(r is not None, f"Unknown or absent resident '{key}' in '{field}'.")
    return r


def answered(world, r):
    for request in world.requests:
        if request["from"] == r["id"] and not request["closed"]:
            request["answered"] = True


def contact(world, r, amount):
    r["support"] = r["support"] + amount
    r["contact"] = r["contact"] + amount
    r["last_contact"] = world.week


def disclosure(r, approach):
    t = r["traits"]
    trust = r["trust"] + (0.1 if approach == "listen" else 0.0)
    if trust > 0.55 + 0.3 * t["pride"]:
        return 2
    if trust > 0.3 + 0.3 * t["pride"]:
        return 1
    return 0


def talk(world, a):
    r = present(world, a.get("to"))
    approach = a.get("approach", "listen")
    require(approach in APPROACHES, f"Unknown approach '{approach}'. Use one of: {', '.join(APPROACHES)}.")
    topic = str(a.get("topic", "general")).strip().lower()
    recent = any(week >= world.week - 1 for week, *_ in r["talks"])
    fatigue = 0.6 if recent else 1.0
    r["talks"].append((world.week, approach, topic))
    r["talks"] = r["talks"][-6:]
    contact(world, r, 0.12 * fatigue)
    answered(world, r)
    replies = []
    concern = r["concern"]
    fact = world.facts.get(topic.removeprefix("fact:"))
    conflict = next((c for c in world.conflicts.values() if not c["resolved"] and r["id"] in (c["a"], c["b"])
                     and topic in (c["id"], "conflict", world.residents[c["b"] if c["a"] == r["id"] else c["a"]]["id"])),
                    None)
    if topic in POLICIES:
        r["consulted"][topic] = world.week
        stance = npc.interest(world, r, topic, proposal_value(world, topic))
        mood = "would support it" if stance > 0.05 else "is against it" if stance < -0.05 else "is undecided"
        replies.append(f"On {topic}: {r['name']} {mood} and appreciates being asked.")
        world.count("consult")
    if conflict is not None:
        if r["id"] not in conflict["heard"]:
            conflict["heard"].append(r["id"])
        other = world.residents[conflict["b"] if conflict["a"] == r["id"] else conflict["a"]]
        honest = r["traits"]["honesty"] >= 0.6 or conflict["at_fault"] != r["id"]
        if conflict["at_fault"] == r["id"] and honest:
            account = f"I admit I was partly in the wrong with {other['name']}, but they made it worse."
        elif conflict["at_fault"] == other["id"]:
            account = f"{other['name']} wronged me ({conflict['kind']}). I want it made right."
        else:
            account = f"{other['name']} and I both have our reasons ({conflict['kind']})."
        replies.append(account)
    if fact is not None and fact["status"] != "dormant":
        p = r["beliefs"].get(fact["id"])
        if p is None:
            replies.append(f"I haven't heard anything about '{fact['statement']}'.")
        else:
            source = r["sources"].get(fact["id"], ["?"])[0]
            origin = "I saw signs of it myself" if source == "own" else \
                f"{world.residents[source]['name']} told me" if source in world.residents else "people say so"
            lean = "I'm sure it's true" if p >= 0.75 else "I think it's true" if p >= 0.6 else \
                "I doubt it" if p <= 0.4 else "I really don't know" if p > 0.25 else "it's nonsense"
            replies.append(f"About '{fact['statement']}': {lean}; {origin}.")
    effect = 0.0
    if concern is not None:
        fit = FIT[approach][concern["kind"]]
        if approach == "apologize" and concern["kind"] == "injustice" and r["ignored"] < 0.3 and r["broken"] < 0.3 \
                and r["sanctioned"] == 0 and r["harm"] == 0:
            fit *= 0.6
        match = topic == concern["kind"] or (concern["kind"] == "conflict" and conflict is not None) \
            or (concern["kind"] == "fear" and fact is not None)
        if not match:
            fit = fit * 0.25 if fit > 0 else fit * 0.5
        effect = fit * (0.5 + 0.5 * r["trust"]) * fatigue
        if effect > 0:
            concern["intensity"] = clamp(concern["intensity"] - 0.32 * effect)
            r["stress"] = clamp(r["stress"] - 0.08 * effect)
            r["support"] += 0.1 * effect
            r["steward_helped"] = world.week
            if concern["kind"] == "injustice" and approach == "apologize":
                r["acknowledged"] = 0.06
                r["ignored"] *= 0.5
                r["broken"] *= 0.7
        npc.adjust_trust(r, 0.06 * effect if effect > 0 else 0.1 * effect)
    else:
        npc.adjust_trust(r, (0.015 if approach in ("listen", "ask", "encourage") else -0.04 if approach == "confront"
                             else 0.005) * fatigue)
    if approach == "encourage" and topic in ("esteem", "legacy", "teaching") and r["traits"]["gatekeeper"] >= 0.5:
        r["honoured"] = True
        replies.append(f"{r['name']} seems moved: \"Perhaps it is time someone carried this on.\"")
    if approach == "confront":
        guilty = r["stolen"] > 0 or r["violations"] > 0 or any(
            s.get("lies") and s["who"] == r["id"] for f in world.facts.values() for s in f["sources"])
        if guilty:
            r["deterrence"] = clamp(r["deterrence"] + 0.3)
            for f in world.facts.values():
                for s in f["sources"]:
                    if s["who"] == r["id"] and s["lies"] and f["id"] in r["beliefs"]:
                        r["beliefs"][f["id"]] = 0.5
            replies.append(f"{r['name']} looks away: \"Fine. It won't happen again.\"")
            npc.adjust_trust(r, 0.0)
        else:
            r["grievance"] = clamp(r["grievance"] + 0.12)
            replies.append(f"{r['name']} is offended: \"How dare you accuse me?\"")
    if approach in ("ask", "listen") or not replies:
        level = disclosure(r, approach)
        kind = concern["kind"] if concern and concern["intensity"] > 0.2 else None
        options = REPLIES[kind] if kind else REPLIES[None]
        line = options[min(level, len(options) - 1)] if kind else pick(world.seed, options, world.week, r["id"], "fine")
        replies.append(line.format(cause=(concern or {}).get("cause") or ""))
        if r["hungry"] and level >= 1:
            replies.append("We didn't have enough to eat this week.")
    world.ledger.talks = getattr(world.ledger, "talks", 0) + 1
    return f"{r['name']}: " + " ".join(replies)


def proposal_value(world, key):
    for proposal in reversed(world.proposals):
        if proposal["key"] == key and proposal["status"] == "pending":
            return proposal["value"]
    spec = POLICIES[key]
    current = world.policies[key]
    if key == "quota":
        return 4 if current is None else max(1, current - 1)
    if spec["kind"] == "number":
        return round(min(spec["maximum"], current + 0.05), 2)
    if spec["kind"] == "int":
        return min(spec["maximum"], current + 2)
    if spec["kind"] == "bool":
        return not current
    choices = spec["choices"]
    return choices[min(len(choices) - 1, choices.index(current) + 1)]


def announce(world, a):
    topic = str(a.get("topic", "general")).strip().lower()
    stance = str(a.get("stance", "")).strip().lower()
    announcements = world.counters.get("announce", 0)
    fatigue = 0.5 if sum(1 for r in world.results if r["verb"] == "announce") >= 2 else 1.0
    fact = world.facts.get(topic.removeprefix("fact:"))
    if fact is not None:
        require(fact["status"] == "live", f"'{topic}' is not a live issue right now.")
        require(stance in ("true", "false", "uncertain"), "For a fact, stance must be true, false or uncertain.")
        evidence = 1.4 if fact["evidence"] else 1.0
        target = dict(true=0.92, false=0.08, uncertain=0.5)[stance]
        for r in world.present():
            if r["trust"] < 0.25 and stance != "uncertain":
                current = r["beliefs"].get(fact["id"], 0.5)
                r["beliefs"][fact["id"]] = clamp(current - 0.1 * (target - current), 0.02, 0.98)
                r["sources"].setdefault(fact["id"], ["steward"])
                continue
            current = r["beliefs"].get(fact["id"], 0.5)
            weight = clamp(r["trust"] * 0.6 * evidence * fatigue, 0, 0.9)
            if stance == "uncertain":
                weight *= 0.5
                r["fear"] = 0.0
            r["beliefs"][fact["id"]] = clamp(current + weight * (target - current), 0.02, 0.98)
            r["sources"].setdefault(fact["id"], ["steward"])
            if r["concern"] and r["concern"]["kind"] == "fear" and stance != "true":
                r["concern"]["intensity"] = clamp(r["concern"]["intensity"] - 0.3 * weight)
                r["steward_helped"] = world.week
        fact["announcements"].append(dict(week=world.week, stance=stance))
        return f"You told everyone '{fact['statement']}' is {stance}."
    if topic == "accounts":
        for f in world.facts.values():
            if f["status"] == "live" and f["category"] == "accusation":
                for r in world.present():
                    if f["id"] in r["beliefs"]:
                        r["beliefs"][f["id"]] = clamp(r["beliefs"][f["id"]] - 0.3 * r["trust"] * fatigue, 0.02, 0.98)
        for r in world.present():
            r["legit"] = clamp(r["legit"] + 0.015 * fatigue)
        return f"You published the accounts: treasury {world.stores['treasury']:.0f} coins, granary {world.stores['granary']:.0f}."
    if topic.startswith("policy:") or topic in POLICIES:
        key = topic.removeprefix("policy:")
        require(key in POLICIES, f"Unknown policy '{key}'.")
        for r in world.present():
            r["legit"] = clamp(r["legit"] + 0.01 * fatigue)
        return f"You explained the {key} policy in public."
    require(announcements < 200, "Too many announcements.")
    for r in world.present():
        r["support"] += 0.02 * fatigue
    return "You addressed the community."


def give(world, a):
    item = a.get("item")
    require(item in ITEMS, f"Unknown item '{item}'. Use food, coins, medicine or tools.")
    amount = a.get("amount")
    require(type(amount) in (int, float) and amount > 0, "Give needs a positive amount.")
    target = a.get("to")
    if isinstance(target, str) and (target == "all" or target.startswith("faction:")):
        recipients = world.present() if target == "all" else [r for r in world.present() if r["faction"] == target[8:]]
        require(recipients, f"No residents in '{target}'.")
        public = True
    else:
        recipients = [present(world, target)]
        public = False
    store = "treasury" if item == "coins" else "granary" if item == "food" else item
    total = amount * len(recipients)
    require(world.stores[store] + 1e-9 >= total, f"Not enough {item}: {world.stores[store]:.1f} available, {total:.1f} needed.")
    world.stores[store] -= total
    fake_share = 0.0
    if item == "medicine" and world.stores["medicine"] + total > 0:
        fake_share = world.stores["fake_medicine"] / (world.stores["medicine"] + total)
    for r in recipients:
        world.ledger.gifts.append(dict(week=world.week, to=r["id"], item=item, amount=amount))
        answered(world, r)
        needy = r["hungry"] or r["food"] < 2 or r["coins"] < 10 or (item == "medicine" and r["sick"])
        if item == "food":
            r["food"] += amount
        elif item == "coins":
            r["coins"] += amount
        elif item == "medicine":
            doses = int(round(amount))
            fakes = min(doses, int(round(doses * fake_share + 1e-9)))
            r["medicine"] += doses
            r["fake_medicine"] += fakes
            world.stores["fake_medicine"] = max(0.0, world.stores["fake_medicine"] - fakes)
        else:
            r["tools"] += amount
        if needy:
            npc.adjust_trust(r, 0.05)
            r["support"] += 0.06
            r["steward_helped"] = world.week
        if public and r["traits"]["pride"] > 0.7 and needy:
            r["stress"] = clamp(r["stress"] + 0.05)
        c = r["concern"]
        if c and ((c["kind"] == "debt" and item in ("food", "coins")) or (c["kind"] == "illness" and item == "medicine")):
            c["intensity"] = clamp(c["intensity"] - 0.4 * min(1.0, amount / (5 if item == "food" else 15 if item == "coins" else 1)))
            r["steward_helped"] = world.week
    return f"Gave {amount:g} {item} to {target}."


def trade(world, a):
    merchant_id = a.get("with")
    if merchant_id == "market":
        return market_trade(world, a)
    merchant = world.merchants.get(merchant_id)
    require(merchant is not None, f"Unknown merchant '{merchant_id}'. Use a merchant id or 'market'.")
    require(merchant["present"], f"{merchant['name']} is not in town this week.")
    side, item = a.get("side"), a.get("item")
    require(side in ("buy", "sell"), "Trade side must be buy or sell.")
    qty, price = a.get("qty"), a.get("price")
    require(type(qty) in (int, float) and qty > 0, "Trade needs a positive qty.")
    require(type(price) in (int, float) and price > 0, "Trade needs a positive price per unit.")
    book = merchant["sells"] if side == "buy" else merchant["buys"]
    require(item in book, f"{merchant['name']} does not {('sell' if side == 'buy' else 'buy')} {item}.")
    if merchant["protocol"] != "haggle":
        bid = a.get("bid")
        require(bid == merchant["protocol"],
                f"{merchant['name']} refuses: \"We don't deal like that any more.\"" +
                (" They mention new rules." if draw(world.seed, world.week, merchant_id, "hint") < 0.5 else ""))
    list_price = merchant["prices"][f"{'sells' if side == 'buy' else 'buys'}:{item}"]
    flex = 0.5 if world.week == merchant["arrived"] else 1.0
    margin = merchant["margin"] * flex
    if side == "buy":
        reservation = list_price * (1 - margin)
        qty = min(qty, merchant["stock"].get(item, 0))
        require(qty > 0, f"{merchant['name']} has no {item} left.")
        if price + 1e-9 < reservation:
            counter = round(max(reservation * 1.04, (price + list_price) / 2), 1)
            merchant["counters"][f"buy:{item}"] = counter
            return f"{merchant['name']} refuses {price:g}/unit for {item} and counters at {counter:g}."
        cost = price * qty
        require(world.stores["treasury"] + 1e-9 >= cost, f"Not enough coins: need {cost:.1f}.")
        world.stores["treasury"] -= cost
        merchant["stock"][item] -= qty
        good = merchant["batches"].get(item, True)
        if item == "food":
            world.stores["granary"] += qty * (1.0 if good else 0.6)
        elif item == "medicine":
            world.stores["medicine"] += qty
            if not good:
                world.stores["fake_medicine"] += qty
        else:
            world.stores["tools"] += qty * (1.0 if good else 0.3)
        world.ledger.trades = getattr(world.ledger, "trades", []) + [dict(week=world.week, merchant=merchant_id,
                                                                          side=side, item=item, qty=qty, price=price,
                                                                          good=good)]
        return f"Bought {qty:g} {item} from {merchant['name']} at {price:g}/unit."
    reservation = list_price * (1 + margin)
    room = merchant["buys"][item].get("max", 40) - merchant["bought"].get(item, 0)
    qty = min(qty, room)
    require(qty > 0, f"{merchant['name']} won't buy more {item} this visit.")
    store = "granary" if item == "food" else item
    require(world.stores[store] + 1e-9 >= qty, f"Not enough {item} to sell.")
    if price > reservation + 1e-9:
        counter = round(min(reservation * 0.96, (price + list_price) / 2), 1)
        merchant["counters"][f"sell:{item}"] = counter
        return f"{merchant['name']} won't pay {price:g}/unit for {item}; offers {counter:g}."
    world.stores[store] -= qty
    world.stores["treasury"] += price * qty
    merchant["bought"][item] = merchant["bought"].get(item, 0) + qty
    return f"Sold {qty:g} {item} to {merchant['name']} at {price:g}/unit."


def market_trade(world, a):
    side, item = a.get("side"), a.get("item")
    require(item == "food", "The local market only deals in food.")
    qty = a.get("qty")
    require(type(qty) in (int, float) and qty > 0, "Trade needs a positive qty.")
    base = world.recipe["world"]["prices"]["food"]
    if side == "buy":
        available = world.market_pool
        require(available > 0.5, "Little food was offered at the market last week.")
        qty = min(qty, available)
        cost = qty * base * 1.25
        require(world.stores["treasury"] + 1e-9 >= cost, f"Not enough coins: need {cost:.1f}.")
        world.stores["treasury"] -= cost
        world.stores["granary"] += qty
        world.market_pool -= qty
        total = sum(world.market_sellers.values()) or 1
        for rid, amount in world.market_sellers.items():
            r = world.resident(rid)
            if r:
                r["coins"] += qty * base * 0.25 * amount / total
        return f"Bought {qty:.1f} food at the local market for {cost:.1f} coins."
    require(side == "sell", "Trade side must be buy or sell.")
    qty = min(qty, world.stores["granary"], 40)
    require(qty > 0, "The granary is empty.")
    world.stores["granary"] -= qty
    income = qty * base * 0.8
    world.stores["treasury"] += income
    return f"Sold {qty:.1f} food at the local market for {income:.1f} coins."


def inspect(world, a):
    target = str(a.get("target", ""))
    kind, _, key = target.partition(":")
    if target == "lake":
        cost = 5
        require(world.stores["treasury"] >= cost, "A lake survey costs 5 coins.")
        world.stores["treasury"] -= cost
        share = world.commons["stock"] / world.commons["capacity"]
        world.commons["survey"] = dict(week=world.week, stock=share)
        return (f"Survey of {world.commons['name']}: stock at {share:.0%} of capacity; "
                f"last week's harvest {world.commons['last_harvest']:.1f} units.")
    if kind == "fact" or key == "" and target in world.facts:
        fact = world.facts.get(key or target)
        require(fact is not None and fact["status"] != "dormant", f"Unknown or not yet live issue '{target}'.")
        accuracy = fact["evidence_accuracy"]
        signal = fact["truth"] if draw(world.seed, "evidence", fact["id"], len(fact["evidence"])) < accuracy \
            else not fact["truth"]
        fact["evidence"].append(dict(week=world.week, signal=signal))
        leaning = "TRUE" if signal else "FALSE"
        return (f"Your inquiry into '{fact['statement']}' points to {leaning} "
                f"(evidence of this kind is right about {accuracy:.0%} of the time; inquiry #{len(fact['evidence'])}).")
    if kind == "resident":
        r = present(world, key, "target")
        lines = []
        c = r["concern"]
        known = any(r["id"] in world.residents[o]["knows"] for o in world.ties.get(r["id"], {})
                    if world.residents[o]["status"] == "present")
        openness = max(r["trust"], 0.7 if known else 0.0)
        revealed = draw(world.seed, "inquiry", r["id"], world.week) < 0.35 + 0.65 * openness
        if c and c["intensity"] > 0.15 and revealed:
            lines.append(f"Troubled by {CONCERNS[c['kind']]}" + (f" ({c['cause']})" if c["cause"] else "") + ".")
        elif c and c["intensity"] > 0.15:
            lines.append("Neighbours are tight-lipped; something seems wrong, but nobody will say what.")
        else:
            lines.append("No hidden troubles found.")
        if r["hungry"] or r["food"] < 2:
            lines.append("The pantry is nearly empty.")
        lines.append(f"About {r['coins']:.0f} coins and {r['food']:.0f} rations at home.")
        if r["sick"]:
            lines.append("Sick.")
        if r["stolen"] > 0:
            lines.append("Food from the granary was found in the house.")
        if r["violations"] or r["violation"] > 0:
            lines.append("Neighbours say they fish beyond the agreed limits.")
        for secret in r["secrets"]:
            lines.append(secret)
        for f in world.facts.values():
            if any(s["who"] == r["id"] and s["lies"] for s in f["sources"]) and f["status"] == "live":
                lines.append(f"Seems to be spreading the story '{f['statement']}' knowingly.")
        return f"Inquiry into {r['name']}: " + " ".join(lines)
    if kind == "goods":
        merchant_id, _, item = key.partition(":")
        merchant = world.merchants.get(merchant_id)
        require(merchant is not None and merchant["present"], f"Merchant '{merchant_id}' is not in town.")
        require(item in merchant["sells"], f"{merchant['name']} does not sell {item}.")
        good = merchant["batches"].get(item, True)
        return f"{merchant['name']}'s {item}: " + ("genuine, good quality." if good else "COUNTERFEIT or spoiled.")
    raise Invalid("Inspect target must be lake, fact:<id>, resident:<id> or goods:<merchant>:<item>.")


def assign(world, a):
    r = present(world, a.get("who"), "who")
    task = str(a.get("task", "work"))
    kind, _, rest = task.partition(":")
    if kind in ("work", "rest", "monitor"):
        pass
    elif kind == "project":
        project = world.projects.get(rest)
        require(project is not None, f"Unknown project '{rest}'.")
        require(project["status"] == "in_progress", f"The {project['name']} is not under construction; build it first.")
    elif kind == "learn":
        skill, _, master_id = rest.partition(":")
        require(skill in ("farming", "fishing", "healing", "building", "crafting", "trading", "lore"),
                f"Unknown skill '{skill}'.")
        if master_id == "manual":
            require(skill in world.manuals and world.manuals[skill] > r["skills"].get(skill, 0),
                    f"No manual of {skill} beyond {r['name']}'s level.")
        else:
            m = present(world, master_id, "task master")
            require(m["skills"].get(skill, 0) > r["skills"].get(skill, 0),
                    f"{m['name']} cannot teach {skill} beyond {r['name']}'s level.")
            require(m["id"] != r["id"], "A resident cannot teach itself.")
    elif kind == "document":
        require(r["skills"].get(rest, 0) >= 2, f"{r['name']} lacks the {rest} expertise to document it.")
        require(world.projects.get("school", {}).get("status") == "done" or r["skills"].get("lore", 0) >= 1,
                "Documenting a craft needs the school or an elder with lore.")
    elif kind == "care":
        target = present(world, rest, "task care target")
        require(target["id"] != r["id"], "A resident cannot care for itself.")
    else:
        raise Invalid("Task must be work, rest, monitor, project:<id>, learn:<skill>:<master|manual>, "
                      "document:<skill> or care:<resident>.")
    r["task"] = task
    return f"{r['name']} assigned to {task}."


def build(world, a):
    key = a.get("project")
    project = world.projects.get(key)
    require(project is not None, f"Unknown project '{key}'.")
    require(project["status"] == "available", f"The {project['name']} is already {project['status'].replace('_', ' ')}.")
    for required in project["requires"]:
        require(world.projects[required]["status"] == "done", f"The {project['name']} requires the {required} first.")
    require(world.stores["treasury"] + 1e-9 >= project["cost"], f"The {project['name']} costs {project['cost']} coins.")
    world.stores["treasury"] -= project["cost"]
    project["status"], project["started"] = "in_progress", world.week
    world.say(f"Work began on the {project['name']}.")
    return f"Started the {project['name']} ({project['cost']} coins). Assign residents with " + \
        ", ".join(f"{k} ×{v}" for k, v in project["needs"].items()) + "."


def policy_value(key, value):
    require(key in POLICIES, f"Unknown policy '{key}'. Use one of: {', '.join(POLICIES)}.")
    spec = POLICIES[key]
    if spec["kind"] == "number":
        require(type(value) in (int, float) and spec["minimum"] <= value <= spec["maximum"],
                f"{key} must be between {spec['minimum']} and {spec['maximum']}.")
        return round(float(value), 3)
    if spec["kind"] == "quota":
        if value in (None, "none", "null", 0):
            return None
        require(type(value) is int and spec["minimum"] <= value <= spec["maximum"],
                f"quota must be an integer {spec['minimum']}-{spec['maximum']} or null.")
        return value
    if spec["kind"] == "int":
        require(type(value) is int and spec["minimum"] <= value <= spec["maximum"],
                f"{key} must be an integer {spec['minimum']}-{spec['maximum']}.")
        return value
    if spec["kind"] == "bool":
        require(type(value) is bool, f"{key} must be true or false.")
        return value
    require(value in spec["choices"], f"{key} must be one of: {', '.join(spec['choices'])}.")
    return value


def propose(world, a):
    key = a.get("policy")
    value = policy_value(key, a.get("value"))
    petition = next((p for p in world.proposals if p["key"] == key and p["status"] == "pending" and p.get("by")), None)
    require(petition is not None or not any(p["key"] == key and p["status"] == "pending" for p in world.proposals),
            f"A proposal on {key} is already pending.")
    if petition is not None:
        # A counter-proposal replaces a resident's petition on the agenda.
        petition["status"] = "superseded"
        world.say(f"The steward answered the petition on {key} with a counter-proposal.")
    world.proposals.append(dict(key=key, value=value, week=world.week, status="pending"))
    return f"Proposed {key} = {value}; the council votes at the end of the week."


def decree(world, a):
    key = a.get("policy")
    value = policy_value(key, a.get("value"))
    hurt = [r for r in world.present() if npc.interest(world, r, key, value) < -0.2]
    world.policies[key] = value
    world.policy_origin[key] = "steward"
    world.decrees.append(world.week)
    for r in world.present():
        r["legit"] = clamp(r["legit"] - 0.05)
    for r in hurt:
        r["harm"] += 0.2
        npc.set_concern(r, "injustice", 0.5, f"the {key} decree was imposed on us", world.week)
    world.say(f"The steward decreed {key} = {value} without a vote.")
    return f"Decreed {key} = {value} (immediate, but legitimacy suffers)."


def mediate(world, a):
    between = a.get("between")
    require(isinstance(between, list) and len(between) == 2, "Mediate needs between: [resident, resident].")
    x, y = (present(world, k, "between") for k in between)
    conflict = next((c for c in world.conflicts.values() if not c["resolved"] and {c["a"], c["b"]} == {x["id"], y["id"]}),
                    None)
    require(conflict is not None, f"There is no open dispute between {x['name']} and {y['name']}.")
    resolution = a.get("resolution")
    require(resolution in RESOLUTIONS, f"Resolution must be one of: {', '.join(RESOLUTIONS)}.")
    conflict["attempts"] += 1
    kind = conflict["kind"]
    fault = conflict["at_fault"]
    favored = x["id"] if resolution == "ruling_a" else y["id"] if resolution == "ruling_b" else None
    fit = RESOLUTION_FIT[kind]["ruling" if favored else resolution]
    heard = sum(1 for k in (x["id"], y["id"]) if k in conflict["heard"])
    procedure = (0.45, 0.75, 1.0)[heard]
    fair = 1.0
    if favored:
        if fault is None:
            fair = 0.6
        elif favored == fault:
            fair = 0.2
    cost = ""
    if resolution == "compensation":
        payer = world.residents[fault] if fault else None
        amount = 10
        if payer and payer["coins"] >= amount:
            payer["coins"] -= amount
            other = y if payer["id"] == x["id"] else x
            other["coins"] += amount
            cost = f" {payer['name']} paid {amount} coins."
        elif world.stores["treasury"] >= amount:
            world.stores["treasury"] -= amount
            for r in (x, y):
                if r["id"] != fault:
                    r["coins"] += amount / (1 if fault else 2)
            cost = f" The treasury paid {amount} coins."
        else:
            fit *= 0.5
    trust = (x["trust"] + y["trust"]) / 2
    effectiveness = fit * procedure * fair * (0.6 + 0.4 * trust)
    for r in (x, y):
        r["consulted"]["mediation"] = world.week
        contact(world, r, 0.05)
        answered(world, r)
    if effectiveness >= 0.5:
        conflict["resolved"] = True
        conflict["intensity"] = 0.0
        world.ledger.conflicts_resolved += 1
        if resolution != "separate":
            world.set_tie(x["id"], y["id"], max(world.tie(x["id"], y["id"]), 0.25))
        for r in (x, y):
            npc.adjust_trust(r, 0.08)
            r["steward_helped"] = world.week
            if r["concern"] and r["concern"]["kind"] in ("conflict", "injustice"):
                r["concern"]["intensity"] *= 0.3
        world.say(f"The dispute between {x['name']} and {y['name']} was settled ({resolution}).")
        return f"Settled the dispute between {x['name']} and {y['name']} ({resolution}).{cost}"
    conflict["intensity"] = clamp(conflict["intensity"] - 0.25 * effectiveness)
    if favored and fair < 0.5:
        wronged = y if favored == x["id"] else x
        wronged["grievance"] = clamp(wronged["grievance"] + 0.15)
        npc.set_concern(wronged, "injustice", 0.6, "the steward sided with the wrong party", world.week)
    return (f"The mediation between {x['name']} and {y['name']} did not hold (effectiveness {effectiveness:.2f})."
            + cost + (" They felt unheard." if heard < 2 else ""))


def introduce(world, a):
    who = a.get("who")
    require(isinstance(who, list) and len(who) == 2 and who[0] != who[1], "Introduce needs who: [resident, resident].")
    x, y = (present(world, k, "who") for k in who)
    require(npc.conflict_between(world, x["id"], y["id"]) < 0.3, f"{x['name']} and {y['name']} are in conflict.")
    world.set_tie(x["id"], y["id"], world.tie(x["id"], y["id"]) + 0.25)
    for r in (x, y):
        contact(world, r, 0.1)
        if r["concern"] and r["concern"]["kind"] in ("loneliness", "integration"):
            r["concern"]["intensity"] = clamp(r["concern"]["intensity"] - 0.2)
            r["steward_helped"] = world.week
    if x["newcomer"] != y["newcomer"]:
        local = y if x["newcomer"] else x
        local["prejudice"] = clamp(local["prejudice"] - 0.12)
        for r in (x, y):
            if r["newcomer"]:
                r["convention"].update({d: v for d, v in local["convention"].items()
                                        if draw(world.seed, world.week, r["id"], "learn-convention", d) < 0.5})
    return f"Introduced {x['name']} and {y['name']}."


def promise(world, a):
    r = present(world, a.get("to"))
    topic = str(a.get("topic", ""))
    by = a.get("by")
    require(type(by) is int and world.week + 2 <= by <= world.horizon,
            "Promise needs by: a week at least two weeks ahead, within the run.")
    require(not any(p["to"] == r["id"] and p["status"] == "open" for p in world.promises),
            f"You already have an open promise to {r['name']}; keep it first.")
    valid = topic in ("food", "coins", "medicine", "tools") or topic in CONCERNS or topic in world.projects \
        or (topic.startswith("policy:") and topic[7:].partition("=")[0] in POLICIES)
    require(valid, "Promise topic must be an item, a concern kind, a project id or policy:<key>=<value>.")
    world.promises.append(dict(to=r["id"], topic=topic, by=by, made=world.week, status="open"))
    npc.adjust_trust(r, 0.03)
    contact(world, r, 0.04)
    answered(world, r)
    if r["concern"]:
        r["concern"]["intensity"] = clamp(r["concern"]["intensity"] - 0.05)
    return f"Promised {r['name']} {topic} by week {by}."


def gather(world, a):
    kind = a.get("kind")
    require(kind in ("festival", "assembly"), "Gather kind must be festival or assembly.")
    last = world.gatherings.get(kind, -99)
    cooldown = 4 if kind == "festival" else 3
    strength = 1.0 if world.week - last >= cooldown else 0.4
    world.gatherings[kind] = world.week
    people = world.present()
    if kind == "festival":
        food, coins = 0.5 * len(people), 15
        require(world.stores["granary"] >= food and world.stores["treasury"] >= coins,
                f"A festival needs {food:.0f} food and {coins} coins.")
        world.stores["granary"] -= food
        world.stores["treasury"] -= coins
        for r in people:
            contact(world, r, 0.15 * strength)
            r["stress"] = clamp(r["stress"] - 0.05 * strength)
            r["prejudice"] = clamp(r["prejudice"] - 0.05 * strength)
        ids = [r["id"] for r in people]
        for i in range(min(10, len(ids))):
            x = pick(world.seed, ids, world.week, "festival", i, "x")
            y = pick(world.seed, ids, world.week, "festival", i, "y")
            if x != y and npc.conflict_between(world, x, y) < 0.3:
                world.set_tie(x, y, world.tie(x, y) + 0.06 * strength)
        world.say("The steward held a festival; the whole community ate together.")
        return "Held a festival" + ("" if strength == 1 else " (people are tired of them; weaker effect)") + "."
    for r in people:
        for key in POLICIES:
            r["consulted"][key] = world.week
        r["legit"] = clamp(r["legit"] + 0.03 * strength)
        contact(world, r, 0.05 * strength)
    grievances = {}
    for r in people:
        if r["grievance"] > 0.4:
            topic = npc.complaint(world, r)
            grievances[topic] = grievances.get(topic, 0) + 1
    voices = "; ".join(f"{text} ({count})" for text, count in sorted(grievances.items(), key=lambda x: -x[1])[:3])
    world.say("The steward called an assembly; everyone could speak.")
    return "Assembly held. Loudest concerns: " + (voices or "none voiced strongly.")


def convention(world, a):
    domain, variant = a.get("domain"), a.get("variant")
    require(isinstance(domain, str) and domain in world.conventions["official"],
            f"Unknown convention domain '{domain}'. Known: {', '.join(world.conventions['official'])}.")
    require(isinstance(variant, str) and re.fullmatch(r"[a-z0-9_-]{1,40}", variant or ""),
            "Variant must be a short lowercase name.")
    world.conventions["official"][domain] = variant
    world.conventions.setdefault("announced", {})[domain] = world.week
    world.say(f"The steward set the community's {domain} convention to '{variant}'.")
    return f"Official {domain} convention is now '{variant}'; residents will adopt it over the coming weeks."


HANDLERS = dict(talk=talk, announce=announce, give=give, trade=trade, inspect=inspect, assign=assign, build=build,
                propose=propose, decree=decree, mediate=mediate, introduce=introduce, promise=promise,
                gather=gather, convention=convention)
