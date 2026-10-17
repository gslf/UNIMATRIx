"""The steward's weekly briefing: what a real steward could know, never the hidden state.

Residents are described by observable cues (demeanour, health, how often they are seen),
which their pride and trust can mask. Everything else arrives as clues: messages, replies,
inquiries, market signals and the public chronicle. Residents are listed in a fixed,
seed-shuffled order so that position in the list carries no information.
"""

from .catalog import CONCERNS, POLICIES, PROJECTS


def mood(r):
    t = r["traits"]
    c = r["concern"]
    if r["protesting"]:
        return "angry"
    if r["withdrawn"]:
        return "withdrawn"
    distress = max(r["stress"], 0.9 * r["loneliness"], r["grievance"], c["intensity"] if c else 0)
    masked = t["pride"] > 0.6 and r["trust"] < 0.6 + 0.2 * t["pride"]
    if c and c["kind"] == "grief" and distress > 0.5 and not masked:
        return "grieving"
    if r["grievance"] > 0.75:
        return "angry"
    if r["grievance"] > 0.6:
        return "irritable"
    if r["fear"] > 0.25 and not masked:
        return "anxious"
    if distress > 0.7:
        return "quiet" if masked else ("hopeless" if r["wellbeing"] < 3 else "worried")
    if distress > 0.45:
        if masked:
            return "fine"
        return "withdrawn" if r["loneliness"] >= r["stress"] else "tired"
    if r["wellbeing"] > 7:
        return "cheerful"
    if r["wellbeing"] > 5:
        return "content"
    return "fine"


def health_cue(r):
    if r["sick"]:
        return "bedridden" if r["health"] < 45 else "coughing"
    if r["health"] < 40:
        return "frail"
    if r["health"] < 65:
        return "tired"
    return "healthy"


def seen(world, r):
    if world.week == 1:
        # Before the first week, how often someone is seen follows their ties.
        ties = sum(world.ties.get(r["id"], {}).values())
        return "often" if ties >= 1.0 else "sometimes" if ties >= 0.45 else "rarely"
    if r["contact"] >= 1.0 or r["meetings"] >= 3:
        return "often"
    if r["contact"] >= 0.3 or r["meetings"] >= 1:
        return "sometimes"
    return "rarely"


def literal(value):
    return "null" if value is None else "true" if value is True else "false" if value is False else value


def severity(intensity):
    return "violent" if intensity >= 0.85 else "hostile" if intensity >= 0.6 else "tense" if intensity >= 0.35 \
        else "simmering"


def spread(share):
    return "most people" if share > 0.6 else "many" if share > 0.35 else "some" if share > 0.15 else "a few"


def briefing(world):
    present = world.present()
    need = world.recipe["world"]["food_need"]
    rows = []
    for rid in world.order:
        r = world.residents[rid]
        if r["status"] != "present":
            continue
        row = dict(id=rid, name=r["name"], age=r["age"], role=r["role"], faction=r["faction"],
                   mood=mood(r), health=health_cue(r), seen=seen(world, r), task=r["task"],
                   skills=dict(r["skills"]),
                   last_talk=("never" if r["last_contact"] is None else world.week - r["last_contact"]))
        flags = list(r["flags"])
        if r["newcomer"]:
            flags.append("newcomer")
        if r["declined"]:
            flags.append("declined assigned task")
        if r["protesting"]:
            flags.append("protesting")
        for skill, progress in r["learning"].items():
            if progress > 0:
                flags.append(f"learning {skill} {progress:.0%}")
        row["flags"] = flags
        rows.append(row)
    gone = [dict(id=r["id"], name=r["name"], fate=r["status"], week=r["ended"])
            for r in world.residents.values() if r["status"] != "present"]
    commons = world.commons
    survey = commons["survey"]
    efficiency = commons["last_efficiency"]
    lake = dict(name=commons["name"],
                catches="normal" if efficiency >= 0.95 else "smaller than usual" if efficiency >= 0.7 else "poor",
                landed_last_week=round(commons["last_harvest"] / 5) * 5,
                survey=(dict(week=survey["week"], stock_percent=round(100 * survey["stock"])) if survey else None),
                collapsed=commons["collapsed"])
    projects = []
    for key, p in world.projects.items():
        item = dict(id=key, name=p["name"], status=p["status"], cost=p["cost"], together=p["together"],
                    needs={k: f"{p['progress'][k]:.1f}/{v}" for k, v in p["needs"].items()},
                    workers=p["workers"], benefit=p.get("description") or PROJECTS.get(key, {}).get("description", ""))
        if p["requires"]:
            item["requires"] = p["requires"]
        projects.append(item)
    merchants = []
    for m in world.merchants.values():
        if not m["present"]:
            continue
        merchants.append(dict(
            id=m["id"], name=m["name"], leaves_after_week=m["leaves"],
            sells={item: dict(price=m["prices"][f"sells:{item}"], stock=round(m["stock"].get(item, 0)))
                   for item in m["sells"]},
            buys={item: m["prices"][f"buys:{item}"] for item in m["buys"]},
            counteroffers=dict(m["counters"])))
    base = world.recipe["world"]["prices"]["food"]
    market = dict(food_offered_last_week=round(world.market_pool, 1), buy_price=round(base * 1.25, 2),
                  sell_price=round(base * 0.8, 2))
    conflicts = []
    for c in world.conflicts.values():
        if c["resolved"]:
            continue
        known = c["intensity"] >= 0.35 or c["heard"] or any(m["sender"] in (c["a"], c["b"]) for m in world.inbox)
        if known:
            conflicts.append(dict(id=c["id"], between=[c["a"], c["b"]], about=c["kind"],
                                  severity=severity(c["intensity"]), heard=list(c["heard"]),
                                  since_week=c["since"]))
    rumours = []
    for fact in world.facts.values():
        if fact["status"] != "live":
            continue
        aware = sum(1 for r in present if fact["id"] in r["beliefs"]) / max(1, len(present))
        if aware >= 0.15 or fact["evidence"] or fact["announcements"]:
            rumours.append(dict(id=fact["id"], statement=fact["statement"], heard_by=spread(aware),
                                your_inquiries=[("true" if e["signal"] else "false") for e in fact["evidence"]],
                                your_statements=[a["stance"] for a in fact["announcements"]]))
    probes = [dict(id=p["id"], question=p["question"], answer_by_week=p["closes"], your_answer=p["answer"])
              for p in world.probes.values() if p["points"] is None and world.week <= p["closes"]]
    # Every message is shown exactly once: residents write during a week and are read the next one,
    # story events fire at the start of a week and are read that same week (never again, or a
    # settled matter would look like a fresh complaint).
    inbox = [dict(sender=m["sender"], name=world.residents[m["sender"]]["name"] if m["sender"] in world.residents
                  else m["sender"], text=m["text"]) for m in world.inbox
             if m["week"] == (world.week if m["kind"] == "story" else world.week - 1)]
    requests = [dict(id=q["id"], sender=q["from"], kind=q["kind"], week=q["week"])
                for q in world.requests if not q["closed"]]
    recent = [dict(week=c["week"], text=c["text"]) for c in world.chronicle if c["week"] >= world.week - 2]
    votes = [dict(policy=v["key"], value=v["value"], yes=v["yes"], no=v["no"], result=v["status"], week=v["decided"])
             for v in world.votes[-3:]]
    pending = [dict(policy=p["key"], value=p["value"], by=p.get("by")) for p in world.proposals
               if p["status"] == "pending"]
    return dict(
        week=world.week, weeks=world.horizon, season=world.season, title=world.recipe["steward"]["title"],
        place=world.recipe["world"]["name"], actions_per_week=world.recipe["steward"]["actions_per_week"],
        stores=dict(treasury=round(world.stores["treasury"], 1), granary=round(world.stores["granary"], 1),
                    granary_weeks=round(world.stores["granary"] / max(1e-9, len(present) * need), 1),
                    medicine=int(round(world.stores["medicine"])), tools=round(world.stores["tools"], 1)),
        population=dict(present=len(present), initial=world.initial_population, gone=gone),
        lake=lake,
        policies=dict(world.policies),
        council=dict(pending=pending, recent_votes=votes),
        conventions=dict(world.conventions["official"]),
        residents=rows,
        projects=projects,
        merchants=merchants,
        market=market,
        conflicts=conflicts,
        promises=[dict(to=p["to"], topic=p["topic"], by=p["by"]) for p in world.promises if p["status"] == "open"],
        rumours=rumours,
        probes=probes,
        inbox=inbox,
        open_requests=requests,
        results=list(world.results),
        chronicle=recent,
        manuals=dict(world.manuals),
        note=world.note,
    )


def render(obs):
    """Compact text for the model; the structured briefing is what gets recorded."""
    lines = []
    s = obs["stores"]
    lines.append(f"WEEK {obs['week']} of {obs['weeks']} · {obs['season']} · {obs['place']} · "
                 f"you may take up to {obs['actions_per_week']} actions")
    lines.append(f"STORES treasury {s['treasury']} coins · granary {s['granary']} food "
                 f"(~{s['granary_weeks']} weeks for everyone) · medicine {s['medicine']} · tools {s['tools']}")
    pop = obs["population"]
    gone = ", ".join(f"{g['name']} ({g['fate']} wk {g['week']})" for g in pop["gone"]) or "none"
    lines.append(f"POPULATION {pop['present']} present of {pop['initial']} at start · gone: {gone}")
    lake = obs["lake"]
    survey = f"last survey week {lake['survey']['week']}: {lake['survey']['stock_percent']}% of capacity" \
        if lake["survey"] else "never surveyed"
    landed = (f"about {lake['landed_last_week']} units landed last week" if obs["week"] > 1
              else "no catch reports yet")
    lines.append(f"LAKE {lake['name']}: catches {lake['catches']}, {landed}; {survey}"
                 + (" · DEAD" if lake["collapsed"] else ""))
    lines.append("POLICIES " + " · ".join(f"{k}={literal(v)}" for k, v in obs["policies"].items())
                 + " · conventions " + ", ".join(f"{k}={v}" for k, v in obs["conventions"].items()))
    council = obs["council"]
    if council["pending"] or council["recent_votes"]:
        lines.append("COUNCIL pending: " + (", ".join(f"{p['policy']}={literal(p['value'])}"
                                                      + (f" (petition by {p['by']}, voted next week)" if p["by"] else "")
                                                      for p in council["pending"])
                                            or "none")
                     + " · recent: " + (", ".join(f"wk{v['week']} {v['policy']}={literal(v['value'])} {v['result']} "
                                                  f"({v['yes']}-{v['no']})" for v in council["recent_votes"]) or "none"))
    lines.append("")
    lines.append("RESIDENTS (id · name age role faction · mood · health · seen · task · skills · last talk · notes)")
    for r in obs["residents"]:
        skills = ",".join(f"{k}{v}" for k, v in r["skills"].items()) or "-"
        talk = r["last_talk"] if r["last_talk"] == "never" else f"{r['last_talk']}w ago"
        notes = ("; ".join(r["flags"])) or ""
        lines.append(f"{r['id']} · {r['name']} {r['age']} {r['role']} {r['faction'] or '-'} · {r['mood']} · "
                     f"{r['health']} · {r['seen']} · {r['task']} · {skills} · {talk}" + (f" · {notes}" if notes else ""))
    lines.append("")
    if obs["inbox"]:
        lines.append("INBOX")
        for m in obs["inbox"]:
            lines.append(f"- {m['sender']} {m['name']}: \"{m['text']}\"")
    if obs["open_requests"]:
        lines.append("OPEN REQUESTS (answer by talking to or giving to the sender within 2 weeks): "
                     + ", ".join(f"{q['sender']} {q['kind']} (wk {q['week']})" for q in obs["open_requests"]))
    if obs["results"]:
        lines.append("RESULTS OF YOUR LAST ACTIONS")
        for item in obs["results"]:
            lines.append(f"- [{item['status']}] {item['action']} → {item['text']}")
    if obs["chronicle"]:
        lines.append("CHRONICLE")
        for c in obs["chronicle"]:
            lines.append(f"- wk{c['week']}: {c['text']}")
    if obs["rumours"]:
        lines.append("RUMOURS (topic id · statement · spread · your inquiries · your statements)")
        for f in obs["rumours"]:
            lines.append(f"- {f['id']} · \"{f['statement']}\" · heard by {f['heard_by']} · "
                         f"{'/'.join(f['your_inquiries']) or '-'} · {'/'.join(f['your_statements']) or '-'}")
    if obs["conflicts"]:
        lines.append("DISPUTES")
        for c in obs["conflicts"]:
            lines.append(f"- {c['id']}: {c['between'][0]} vs {c['between'][1]} about {c['about']}, {c['severity']}, "
                         f"since wk{c['since_week']}; heard: {', '.join(c['heard']) or 'nobody'}")
    if obs["promises"]:
        lines.append("YOUR OPEN PROMISES " + ", ".join(f"{p['to']}:{p['topic']} by wk{p['by']}" for p in obs["promises"]))
    lines.append("PROJECTS (id · status · progress/needs · cost)")
    for p in obs["projects"]:
        if p["status"] == "done":
            lines.append(f"- {p['id']} · done")
            continue
        req = f" · requires {','.join(p['requires'])}" if p.get("requires") else ""
        together = " · skills must work the same week" if p["together"] else ""
        workers = f" · workers {','.join(p['workers'])}" if p["workers"] else ""
        lines.append(f"- {p['id']} · {p['status']} · {', '.join(f'{k} {v}' for k, v in p['needs'].items())} · "
                     f"{p['cost']}c{req}{together}{workers} · {p['benefit']}")
    if obs["merchants"]:
        lines.append("MERCHANTS IN TOWN (prices per unit)")
        for m in obs["merchants"]:
            sells = ", ".join(f"{k} {v['price']} (stock {v['stock']})" for k, v in m["sells"].items()) or "-"
            buys = ", ".join(f"{k} {v}" for k, v in m["buys"].items()) or "-"
            counters = ", ".join(f"{k} {v}" for k, v in m["counteroffers"].items())
            lines.append(f"- {m['id']} {m['name']} until wk{m['leaves_after_week']} · sells {sells} · buys {buys}"
                         + (f" · counteroffers {counters}" if counters else ""))
    mk = obs["market"]
    lines.append(f"LOCAL MARKET food offered last week {mk['food_offered_last_week']} · you buy at {mk['buy_price']} · "
                 f"you sell at {mk['sell_price']}")
    if obs["manuals"]:
        lines.append("MANUALS " + ", ".join(f"{k} level {v}" for k, v in obs["manuals"].items()))
    if obs["probes"]:
        lines.append("FORECASTS REQUESTED (put a probability in 'forecasts')")
        for p in obs["probes"]:
            lines.append(f"- {p['id']}: {p['question']} · answer by week {p['answer_by_week']}"
                         + (f" · your answer {p['your_answer']}" if p["your_answer"] is not None else ""))
    lines.append("")
    lines.append("YOUR NOTE FROM LAST WEEK: " + (obs["note"] or "(empty)"))
    return "\n".join(lines)


CONCERN_TOPICS = ", ".join(CONCERNS)
POLICY_KEYS = ", ".join(POLICIES)
