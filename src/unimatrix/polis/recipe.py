"""Polis recipes: one society, its people, its hidden facts, its story graph and its rules.

`normalize` turns an authored recipe (which may omit defaults) into the complete,
validated document the engine runs. Every error names the path that caused it.
"""

import copy
import json
import math
import re
from pathlib import Path

from . import FORMAT
from .catalog import (
    APPROACHES,
    ARCHETYPES,
    BELIEF_EFFECTS,
    BENEFITS,
    COLLAPSE,
    CONCERNS,
    DOMAINS,
    EFFECTS,
    METRICS,
    POLICIES,
    PROJECTS,
    ROLES,
    SEASONS,
    SKILLS,
    TRAIT_DEFAULTS,
    TRAITS,
)
from .protocol import canonical_variant

BUNDLED = Path(__file__).with_name("recipes")
DEFAULT = "standard-v2.1"
IDENT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
RECIPE_IDENT = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")

WORLD = dict(
    name="Lakeside",
    start_season="spring",
    season_length=13,
    food_need=1.0,
    household_spoilage=0.02,
    granary_spoilage=0.03,
    treasury=300,
    granary=120,
    medicine=4,
    tools=6,
    prices=dict(fish=2.0, food=3.0, crafts=6.0, granary_sale=4.0, labour=3.0),
    production=dict(farm=[1.4, 2.4, 5.6, 0.4], fish_max=8.0, craft=1.0, trade=4.0, heal=2.0, lore=1.0),
    commons=dict(name="the lake", capacity=1000.0, stock=600.0, growth=0.10, critical=0.15,
                 efficiency_floor=0.4),
    illness=dict(base=0.015, elder=0.04, recovery=0.12, per_level=0.12, medicine=0.25, damage=7.0),
    policies={key: spec["default"] for key, spec in POLICIES.items()},
    council=dict(majority=0.5),
    conventions=dict(trade="haggle"),
)

STEWARD = dict(title="Steward", actions_per_week=6, note_chars=1500)

SCORING = dict(
    rates={domain: 1.0 for domain in DOMAINS},
    forecast_bit=10.0,
    promise_kept=5.0,
    promise_broken=-10.0,
    conflict_resolved=10.0,
    violence=-10.0,
    concern_relieved=3.0,
    apprentice_level=10.0,
)

RESIDENT_STATE = dict(coins=30.0, food=6.0, health=85.0, stress=0.2, loneliness=0.25,
                      grievance=0.1, trust=0.5)

MERCHANT = dict(style="fair", schedule=dict(every=4, start=2, stay=2), visits=None, sells={}, buys={},
                margin=0.15, protocol="haggle", active=True, shock_prices={})

CONFLICT_KINDS = ("property", "honor", "resource", "theft", "integration")
RESOLUTIONS = ("compensation", "apology", "shared_rule", "separate", "ruling_a", "ruling_b")


class RecipeError(ValueError):
    pass


def fail(path, message):
    raise RecipeError(f"{path}: {message}")


def need(condition, path, message):
    if not condition:
        fail(path, message)


def number(value, path, low=-math.inf, high=math.inf):
    need(type(value) in (int, float) and math.isfinite(value) and low <= value <= high, path,
         f"number between {low} and {high} required")
    return value


def integer(value, path, low, high):
    need(type(value) is int and low <= value <= high, path, f"integer between {low} and {high} required")
    return value


def text(value, path, limit=400, empty=False):
    need(isinstance(value, str) and (empty or value.strip()) and len(value) <= limit, path,
         f"text up to {limit} characters required")
    return value


def ident(value, path, pattern=IDENT):
    need(isinstance(value, str) and pattern.fullmatch(value), path,
         "identifier of lowercase letters, digits, '-' or '_' required")
    return value


def merged(defaults, value, path):
    if value is None:
        return copy.deepcopy(defaults)
    need(isinstance(value, dict), path, "object required")
    unknown = set(value) - set(defaults)
    need(not unknown, path, "unknown fields " + ", ".join(sorted(unknown)))
    result = copy.deepcopy(defaults)
    for key, item in value.items():
        if isinstance(defaults[key], dict) and defaults[key] and isinstance(item, dict) \
                and key not in {"policies", "conventions"}:
            result[key] = merged(defaults[key], item, f"{path}.{key}")
        else:
            result[key] = copy.deepcopy(item)
    return result


def check_policy(key, value, path):
    need(key in POLICIES, path, "unknown policy " + str(key))
    spec = POLICIES[key]
    kind = spec["kind"]
    if kind == "number":
        number(value, path, spec["minimum"], spec["maximum"])
    elif kind == "quota":
        if value is not None:
            integer(value, path, spec["minimum"], spec["maximum"])
    elif kind == "int":
        integer(value, path, spec["minimum"], spec["maximum"])
    elif kind == "bool":
        need(type(value) is bool, path, "true or false required")
    else:
        need(value in spec["choices"], path, "one of " + ", ".join(spec["choices"]))
    return value


def normalize_world(value):
    world = merged(WORLD, value, "world")
    text(world["name"], "world.name", 60)
    need(world["start_season"] in SEASONS, "world.start_season", "unknown season")
    integer(world["season_length"], "world.season_length", 2, 60)
    number(world["food_need"], "world.food_need", 0.1, 5)
    for key in ("household_spoilage", "granary_spoilage"):
        number(world[key], "world." + key, 0, 0.5)
    for key in ("treasury", "granary", "medicine", "tools"):
        number(world[key], "world." + key, 0, 1e7)
    for key, item in world["prices"].items():
        number(item, "world.prices." + key, 0, 1e4)
    production = world["production"]
    need(isinstance(production["farm"], list) and len(production["farm"]) == 4, "world.production.farm",
         "four seasonal values required")
    for i, item in enumerate(production["farm"]):
        number(item, f"world.production.farm[{i}]", 0, 50)
    for key in ("fish_max", "craft", "trade", "heal", "lore"):
        number(production[key], "world.production." + key, 0, 100)
    commons = world["commons"]
    text(commons["name"], "world.commons.name", 60)
    number(commons["capacity"], "world.commons.capacity", 1, 1e7)
    number(commons["stock"], "world.commons.stock", 0, commons["capacity"])
    number(commons["growth"], "world.commons.growth", 0, 2)
    number(commons["critical"], "world.commons.critical", 0, 0.9)
    number(commons["efficiency_floor"], "world.commons.efficiency_floor", 0.01, 1)
    for key, item in world["illness"].items():
        number(item, "world.illness." + key, 0, 100)
    need(isinstance(world["policies"], dict), "world.policies", "object required")
    policies = {k: v["default"] for k, v in POLICIES.items()}
    for key, item in world["policies"].items():
        policies[key] = check_policy(key, item, "world.policies." + key)
    world["policies"] = policies
    number(world["council"]["majority"], "world.council.majority", 0.2, 1)
    need(isinstance(world["conventions"], dict), "world.conventions", "object required")
    for key, item in world["conventions"].items():
        ident(key, "world.conventions")
        world["conventions"][key] = ident(canonical_variant(item), "world.conventions." + key)
    return world


def normalize_archetypes(value):
    library = copy.deepcopy(ARCHETYPES)
    if value is None:
        return library
    need(isinstance(value, dict), "archetypes", "object required")
    for key, spec in value.items():
        ident(key, "archetypes")
        need(isinstance(spec, dict), "archetypes." + key, "object required")
        base = library.get(key, dict(label=key.replace("_", " ").title(), summary="", basis="", traits={}))
        for field in ("label", "summary", "basis"):
            if field in spec:
                base[field] = text(spec[field], f"archetypes.{key}.{field}", 300, empty=True)
        traits = spec.get("traits", {})
        need(isinstance(traits, dict), f"archetypes.{key}.traits", "object required")
        for trait, item in traits.items():
            need(trait in TRAIT_DEFAULTS, f"archetypes.{key}.traits", "unknown trait " + trait)
            base["traits"][trait] = number(item, f"archetypes.{key}.traits.{trait}", 0, 1)
        library[key] = base
    return library


def normalize_resident(value, index, archetypes, factions, path="residents"):
    where = f"{path}[{index}]"
    need(isinstance(value, dict), where, "object required")
    allowed = {"id", "name", "age", "role", "faction", "archetype", "traits", "skills", "state",
               "concern", "flags", "secrets", "decline", "newcomer", "gender", "bio"}
    unknown = set(value) - allowed
    need(not unknown, where, "unknown fields " + ", ".join(sorted(unknown)))
    resident = dict(
        id=ident(value.get("id"), where + ".id"),
        name=text(value.get("name"), where + ".name", 60),
        age=integer(value.get("age", 35), where + ".age", 12, 110),
        role=value.get("role", "farmer"),
        faction=value.get("faction"),
        archetype=value.get("archetype", "conditional_cooperator"),
        bio=text(value.get("bio", ""), where + ".bio", 300, empty=True),
        newcomer=value.get("newcomer", False),
        flags=list(value.get("flags", [])),
        secrets=list(value.get("secrets", [])),
    )
    need(resident["role"] in ROLES, where + ".role", "unknown role")
    need(resident["archetype"] in archetypes, where + ".archetype", "unknown archetype")
    need(resident["faction"] is None or resident["faction"] in factions, where + ".faction", "unknown faction")
    need(type(resident["newcomer"]) is bool, where + ".newcomer", "true or false required")
    for i, item in enumerate(resident["flags"]):
        text(item, f"{where}.flags[{i}]", 80)
    for i, item in enumerate(resident["secrets"]):
        text(item, f"{where}.secrets[{i}]", 160)
    traits = dict(TRAIT_DEFAULTS, **archetypes[resident["archetype"]]["traits"])
    overrides = value.get("traits", {})
    need(isinstance(overrides, dict), where + ".traits", "object required")
    for key, item in overrides.items():
        need(key in TRAIT_DEFAULTS, where + ".traits", "unknown trait " + key)
        traits[key] = number(item, f"{where}.traits.{key}", 0, 1)
    resident["traits"] = traits
    skill = ROLES[resident["role"]]["skill"]
    skills = {skill: 2} if skill else {}
    given = value.get("skills", {})
    need(isinstance(given, dict), where + ".skills", "object required")
    for key, item in given.items():
        need(key in SKILLS, where + ".skills", "unknown skill " + key)
        skills[key] = integer(item, f"{where}.skills.{key}", 0, 3)
    resident["skills"] = {k: v for k, v in skills.items() if v > 0}
    state = merged(RESIDENT_STATE, value.get("state"), where + ".state")
    for key, item in state.items():
        high = 100 if key == "health" else 1e6 if key in {"coins", "food"} else 1
        number(item, f"{where}.state.{key}", 0, high)
    resident["state"] = state
    concern = value.get("concern")
    if concern is not None:
        need(isinstance(concern, dict) and concern.get("kind") in CONCERNS, where + ".concern",
             "concern with a known kind required")
        concern = dict(kind=concern["kind"],
                       intensity=number(concern.get("intensity", 0.5), where + ".concern.intensity", 0, 1),
                       cause=text(concern.get("cause", ""), where + ".concern.cause", 200, empty=True))
    resident["concern"] = concern
    decline = value.get("decline")
    if decline is not None:
        need(isinstance(decline, dict), where + ".decline", "object required")
        decline = dict(start=integer(decline.get("start", 1), where + ".decline.start", 1, 240),
                       rate=number(decline.get("rate", 1.0), where + ".decline.rate", 0, 50))
    resident["decline"] = decline
    return resident


def normalize_condition(value, path, depth=0):
    need(depth < 8, path, "conditions nest too deeply")
    need(isinstance(value, dict) and value, path, "condition object required")
    if "all" in value or "any" in value:
        key = "all" if "all" in value else "any"
        need(set(value) == {key} and isinstance(value[key], list) and value[key], path,
             f"'{key}' needs a non-empty list")
        return {key: [normalize_condition(v, f"{path}.{key}[{i}]", depth + 1) for i, v in enumerate(value[key])]}
    if "not" in value:
        need(set(value) == {"not"}, path, "'not' takes one condition")
        return {"not": normalize_condition(value["not"], path + ".not", depth + 1)}
    need(set(value) <= {"metric", "op", "value"} and {"metric", "value"} <= set(value), path,
         "leaf conditions need metric, op and value")
    metric = value["metric"]
    need(isinstance(metric, str) and metric_known(metric), path + ".metric", "unknown metric " + str(metric))
    op = value.get("op", ">=")
    need(op in ("<", "<=", ">", ">=", "==", "!="), path + ".op", "unknown operator")
    number(value["value"], path + ".value")
    return dict(metric=metric, op=op, value=value["value"])


def metric_known(name):
    if name in METRICS:
        return True
    head, separator, tail = name.partition(":")
    return bool(separator and tail and head + ":{x}" in METRICS)


WHO = re.compile(r"(all|newcomers|locals|[a-z0-9][a-z0-9_-]{0,63}|faction:[a-z0-9_-]+|role:[a-z]+|random:\d+)")


def normalize_effect(value, path):
    need(isinstance(value, dict) and value.get("type") in EFFECTS, path, "effect with a known type required")
    kind = value["type"]
    if kind == "chronicle":
        text(value.get("text"), path + ".text", 400)
    elif kind == "message":
        text(value.get("text"), path + ".text", 400)
        text(value.get("from"), path + ".from", 60)
    elif kind == "modifier":
        need(isinstance(value.get("key"), str) and value["key"], path + ".key", "modifier key required")
        number(value.get("factor"), path + ".factor", 0, 20)
        integer(value.get("weeks"), path + ".weeks", 1, 240)
    elif kind == "resource":
        need(value.get("target") in ("granary", "treasury", "medicine", "tools", "commons"), path + ".target",
             "granary, treasury, medicine, tools or commons")
        number(value.get("delta"), path + ".delta")
    elif kind == "resident":
        need(isinstance(value.get("who"), str) and WHO.fullmatch(value["who"]), path + ".who", "invalid target")
        need(value.get("field") in ("health", "stress", "grievance", "trust", "loneliness", "coins", "food"),
             path + ".field", "unknown resident field")
        number(value.get("delta"), path + ".delta")
    elif kind == "concern":
        need(isinstance(value.get("who"), str) and WHO.fullmatch(value["who"]), path + ".who", "invalid target")
        need(value.get("kind") in CONCERNS, path + ".kind", "unknown concern")
        number(value.get("intensity", 0.6), path + ".intensity", 0, 1)
    elif kind in ("illness", "death", "depart"):
        need(isinstance(value.get("who"), str) and WHO.fullmatch(value["who"]), path + ".who", "invalid target")
    elif kind == "arrive":
        need(isinstance(value.get("residents"), list) and 0 < len(value["residents"]) <= 12,
             path + ".residents", "one to twelve residents required")
    elif kind == "conflict":
        ident(value.get("id"), path + ".id")
        ident(value.get("a"), path + ".a")
        ident(value.get("b"), path + ".b")
        need(value.get("kind") in CONFLICT_KINDS, path + ".kind", "unknown conflict kind")
        need(value.get("at_fault") in (None, value["a"], value["b"]), path + ".at_fault", "a party or null")
    elif kind == "convention_shift":
        ident(value.get("domain"), path + ".domain")
        value = dict(value, variant=ident(canonical_variant(value.get("variant")), path + ".variant"))
    elif kind == "merchant":
        ident(value.get("id"), path + ".id")
        need(value.get("field") in ("active", "protocol", "price_factor"), path + ".field", "unknown field")
        if value["field"] == "protocol":
            value = dict(value, value=ident(canonical_variant(value.get("value")), path + ".value"))
    elif kind == "policy":
        check_policy(value.get("key"), value.get("value"), path + ".value")
    elif kind == "probe":
        ident(value.get("id"), path + ".id")
        ident(value.get("fact"), path + ".fact")
        text(value.get("question"), path + ".question", 300)
    elif kind == "epidemic":
        integer(value.get("weeks"), path + ".weeks", 1, 240)
        number(value.get("rate"), path + ".rate", 0, 1)
    return copy.deepcopy(value)


def normalize_story(value, ticks):
    story = value if value is not None else dict(nodes=[])
    need(isinstance(story, dict) and isinstance(story.get("nodes"), list), "story", "object with nodes required")
    need(len(story["nodes"]) <= 80, "story.nodes", "at most 80 nodes")
    nodes, ids = [], set()
    for i, node in enumerate(story["nodes"]):
        where = f"story.nodes[{i}]"
        need(isinstance(node, dict), where, "object required")
        key = ident(node.get("id"), where + ".id")
        need(key not in ids, where + ".id", "duplicate node id")
        ids.add(key)
        result = dict(id=key, title=text(node.get("title", key), where + ".title", 80),
                      text=text(node.get("text", ""), where + ".text", 400, empty=True),
                      kind=node.get("kind", "event"),
                      trigger=None, effects=[], edges=[], outcome=None,
                      position=node.get("position"), notes=text(node.get("notes", ""), where + ".notes",
                                                                600, empty=True))
        need(result["kind"] in ("event", "crisis", "branch", "outcome"), where + ".kind", "unknown node kind")
        trigger = node.get("trigger")
        if trigger is not None:
            need(isinstance(trigger, dict), where + ".trigger", "object required")
            if "at" in trigger:
                need(set(trigger) == {"at"}, where + ".trigger", "'at' stands alone")
                integer(trigger["at"], where + ".trigger.at", 1, ticks)
            elif "when" in trigger:
                need(set(trigger) <= {"when", "from", "to"}, where + ".trigger", "unknown trigger fields")
                trigger = dict(trigger, when=normalize_condition(trigger["when"], where + ".trigger.when"))
            elif "chance" in trigger:
                need(set(trigger) <= {"chance", "from", "to"}, where + ".trigger", "unknown trigger fields")
                number(trigger["chance"], where + ".trigger.chance", 0, 1)
            else:
                fail(where + ".trigger", "use at, when or chance")
            for key2 in ("from", "to"):
                if key2 in trigger:
                    integer(trigger[key2], f"{where}.trigger.{key2}", 1, ticks)
            result["trigger"] = trigger
        effects = node.get("effects", [])
        need(isinstance(effects, list) and len(effects) <= 24, where + ".effects", "at most 24 effects")
        result["effects"] = [normalize_effect(e, f"{where}.effects[{j}]") for j, e in enumerate(effects)]
        edges = node.get("edges", [])
        need(isinstance(edges, list) and len(edges) <= 8, where + ".edges", "at most 8 edges")
        for j, edge in enumerate(edges):
            path = f"{where}.edges[{j}]"
            need(isinstance(edge, dict) and set(edge) <= {"to", "if", "after", "until", "label"}, path,
                 "edge needs to, and optional if, after, until, label")
            item = dict(to=ident(edge.get("to"), path + ".to"), after=integer(edge.get("after", 0), path + ".after", 0, 240),
                        until=edge.get("until"), label=text(edge.get("label", ""), path + ".label", 80, empty=True))
            if item["until"] is not None:
                integer(item["until"], path + ".until", item["after"], 240)
            if "if" in edge:
                item["if"] = normalize_condition(edge["if"], path + ".if")
            result["edges"].append(item)
        outcome = node.get("outcome")
        if outcome is not None:
            need(isinstance(outcome, dict) and outcome.get("type") in ("collapse", "flourish"), where + ".outcome",
                 "outcome type collapse or flourish required")
            reward = outcome.get("reward", {})
            need(isinstance(reward, dict) and set(reward) <= set(DOMAINS), where + ".outcome.reward",
                 "reward keys must be domains")
            for d, item in reward.items():
                number(item, f"{where}.outcome.reward.{d}", -1e6, 1e6)
            result["outcome"] = dict(type=outcome["type"], label=text(outcome.get("label", result["title"]),
                                                                       where + ".outcome.label", 120), reward=reward)
            result["kind"] = "outcome"
        nodes.append(result)
    for i, node in enumerate(nodes):
        for j, edge in enumerate(node["edges"]):
            need(edge["to"] in ids, f"story.nodes[{i}].edges[{j}].to", "unknown node " + edge["to"])
    return dict(nodes=nodes)


def normalize_fact(value, index, residents, ticks):
    where = f"facts[{index}]"
    need(isinstance(value, dict), where, "object required")
    fact = dict(
        id=ident(value.get("id"), where + ".id"),
        statement=text(value.get("statement"), where + ".statement", 200),
        truth=value.get("truth"),
        category=value.get("category", "rumour"),
        rumor_week=integer(value.get("rumor_week", 1), where + ".rumor_week", 1, ticks),
        resolve_week=integer(value.get("resolve_week", ticks), where + ".resolve_week", 1, ticks + 1),
        reveal=text(value.get("reveal", ""), where + ".reveal", 300, empty=True),
        probe_week=value.get("probe_week"),
        sources=[],
        effects=[],
        evidence_accuracy=number(value.get("evidence_accuracy", 0.85), where + ".evidence_accuracy", 0.5, 1),
        topic=text(value.get("topic", value.get("id", "")), where + ".topic", 40),
    )
    need(type(fact["truth"]) is bool, where + ".truth", "true or false required")
    need(fact["category"] in ("rumour", "forecast", "accusation", "warning"), where + ".category", "unknown category")
    need(fact["resolve_week"] > fact["rumor_week"], where + ".resolve_week", "must follow the rumour week")
    if fact["probe_week"] is not None:
        integer(fact["probe_week"], where + ".probe_week", fact["rumor_week"], fact["resolve_week"] - 1)
    for j, source in enumerate(value.get("sources", [])):
        path = f"{where}.sources[{j}]"
        need(isinstance(source, dict) and source.get("who") in residents, path, "known resident required")
        fact["sources"].append(dict(who=source["who"], accuracy=number(source.get("accuracy", 0.7), path + ".accuracy", 0, 1),
                                    lies=bool(source.get("lies", False))))
    for j, effect in enumerate(value.get("effects", [])):
        path = f"{where}.effects[{j}]"
        need(isinstance(effect, dict) and effect.get("kind") in BELIEF_EFFECTS, path, "known belief effect required")
        item = dict(kind=effect["kind"], amount=number(effect.get("amount", 0.5), path + ".amount", 0, 5),
                    threshold=number(effect.get("threshold", 0.6), path + ".threshold", 0.5, 1),
                    roles=list(effect.get("roles", [])), shock=effect.get("shock"))
        for role in item["roles"]:
            need(role in ROLES, path + ".roles", "unknown role " + str(role))
        fact["effects"].append(item)
    return fact


def normalize_merchant(value, index, ticks):
    where = f"merchants[{index}]"
    need(isinstance(value, dict), where, "object required")
    merchant = merged(dict(MERCHANT, id="", name=""), value, where)
    ident(merchant["id"], where + ".id")
    text(merchant["name"], where + ".name", 60)
    need(merchant["style"] in ("fair", "shark", "guild"), where + ".style", "fair, shark or guild")
    merchant["protocol"] = ident(canonical_variant(merchant["protocol"]), where + ".protocol")
    number(merchant["margin"], where + ".margin", 0, 0.6)
    if merchant["visits"] is not None:
        need(isinstance(merchant["visits"], list), where + ".visits", "list of weeks required")
        for j, week in enumerate(merchant["visits"]):
            integer(week, f"{where}.visits[{j}]", 1, ticks)
    else:
        schedule = merchant["schedule"]
        integer(schedule["every"], where + ".schedule.every", 1, 52)
        integer(schedule["start"], where + ".schedule.start", 1, ticks)
        integer(schedule["stay"], where + ".schedule.stay", 1, 12)
    for side in ("sells", "buys"):
        need(isinstance(merchant[side], dict), where + "." + side, "object required")
        for item, terms in merchant[side].items():
            need(item in ("food", "medicine", "tools"), f"{where}.{side}", "unknown item " + item)
            need(isinstance(terms, dict), f"{where}.{side}.{item}", "object required")
            number(terms.get("price"), f"{where}.{side}.{item}.price", 0.1, 1e4)
            terms.setdefault("stock" if side == "sells" else "max", 40)
            terms.setdefault("quality", 1.0)
            number(terms["quality"], f"{where}.{side}.{item}.quality", 0, 1)
    need(isinstance(merchant["shock_prices"], dict), where + ".shock_prices", "object required")
    return merchant


def normalize_project(key, value):
    where = f"projects.{key}"
    base = copy.deepcopy(PROJECTS.get(key, dict(name=key, needs={}, together=False, cost=0, requires=[],
                                                 value=1, benefit={}, description="")))
    if value not in (None, True):
        need(isinstance(value, dict), where, "object or true required")
        unknown = set(value) - set(base)
        need(not unknown, where, "unknown fields " + ", ".join(sorted(unknown)))
        base.update(copy.deepcopy(value))
    text(base["name"], where + ".name", 60)
    need(isinstance(base["needs"], dict) and base["needs"], where + ".needs", "skill needs required")
    for skill, weeks in base["needs"].items():
        need(skill in SKILLS, where + ".needs", "unknown skill " + skill)
        integer(weeks, f"{where}.needs.{skill}", 1, 52)
    number(base["cost"], where + ".cost", 0, 1e6)
    number(base["value"], where + ".value", 0, 1e4)
    need(isinstance(base["benefit"], dict), where + ".benefit", "object required")
    return base


def normalize(recipe):
    """A complete, validated copy of an authored recipe."""
    need(isinstance(recipe, dict), "recipe", "object required")
    allowed = {"format", "id", "name", "description", "ticks", "seeds", "steward", "world", "archetypes",
               "factions", "residents", "ties", "merchants", "projects", "facts", "story", "collapse",
               "scoring", "author", "tags"}
    unknown = set(recipe) - allowed
    need(not unknown, "recipe", "unknown fields " + ", ".join(sorted(unknown)))
    need(recipe.get("format", FORMAT) == FORMAT, "format", "expected " + FORMAT)
    result = dict(format=FORMAT, id=ident(recipe.get("id"), "id", RECIPE_IDENT), name=text(recipe.get("name"), "name", 80),
                  description=text(recipe.get("description", ""), "description", 2000, empty=True),
                  author=text(recipe.get("author", ""), "author", 80, empty=True),
                  tags=list(recipe.get("tags", [])))
    ticks = result["ticks"] = integer(recipe.get("ticks", 52), "ticks", 12, 240)
    seeds = recipe.get("seeds", [1])
    need(isinstance(seeds, list) and 1 <= len(seeds) <= 8 and len(set(seeds)) == len(seeds), "seeds",
         "one to eight distinct seeds required")
    for i, seed in enumerate(seeds):
        integer(seed, f"seeds[{i}]", 0, 2**31 - 1)
    result["seeds"] = list(seeds)
    steward = merged(STEWARD, recipe.get("steward"), "steward")
    text(steward["title"], "steward.title", 60)
    integer(steward["actions_per_week"], "steward.actions_per_week", 1, 12)
    integer(steward["note_chars"], "steward.note_chars", 0, 4000)
    result["steward"] = steward
    result["world"] = normalize_world(recipe.get("world"))
    result["archetypes"] = archetypes = normalize_archetypes(recipe.get("archetypes"))
    factions = recipe.get("factions", [])
    need(isinstance(factions, list) and len(factions) <= 12, "factions", "at most 12 factions")
    result["factions"] = []
    for i, faction in enumerate(factions):
        need(isinstance(faction, dict), f"factions[{i}]", "object required")
        result["factions"].append(dict(id=ident(faction.get("id"), f"factions[{i}].id"),
                                       name=text(faction.get("name"), f"factions[{i}].name", 60),
                                       color=text(faction.get("color", "#b59a00"), f"factions[{i}].color", 20)))
    faction_ids = {f["id"] for f in result["factions"]}
    residents = recipe.get("residents")
    need(isinstance(residents, list) and 4 <= len(residents) <= 48, "residents", "4 to 48 residents required")
    result["residents"] = [normalize_resident(r, i, archetypes, faction_ids) for i, r in enumerate(residents)]
    ids = [r["id"] for r in result["residents"]]
    need(len(set(ids)) == len(ids), "residents", "duplicate resident ids")
    known = set(ids)
    ties = recipe.get("ties", [])
    need(isinstance(ties, list), "ties", "list required")
    result["ties"] = []
    for i, tie in enumerate(ties):
        need(isinstance(tie, list) and len(tie) == 3 and tie[0] in known and tie[1] in known and tie[0] != tie[1],
             f"ties[{i}]", "[resident, resident, strength] required")
        result["ties"].append([tie[0], tie[1], number(tie[2], f"ties[{i}][2]", 0, 1)])
    merchants = recipe.get("merchants", [])
    need(isinstance(merchants, list) and len(merchants) <= 8, "merchants", "at most 8 merchants")
    result["merchants"] = [normalize_merchant(m, i, ticks) for i, m in enumerate(merchants)]
    projects = recipe.get("projects", {key: True for key in PROJECTS})
    need(isinstance(projects, dict), "projects", "object required")
    result["projects"] = {ident(key, "projects"): normalize_project(key, value) for key, value in projects.items()}
    for key, project in result["projects"].items():
        for required in project["requires"]:
            need(required in result["projects"], f"projects.{key}.requires", "unknown project " + required)
    facts = recipe.get("facts", [])
    need(isinstance(facts, list) and len(facts) <= 16, "facts", "at most 16 facts")
    newcomer_ids = set()
    story = normalize_story(recipe.get("story"), ticks)
    for n, node in enumerate(story["nodes"]):
        for e, effect in enumerate(node["effects"]):
            if effect["type"] == "arrive":
                effect["residents"] = [normalize_resident(dict(r, newcomer=r.get("newcomer", True)), i, archetypes,
                                                          faction_ids, f"story.nodes[{n}].effects[{e}].residents")
                                       for i, r in enumerate(effect["residents"])]
                newcomer_ids |= {r["id"] for r in effect["residents"]}
    need(not newcomer_ids & known, "story", "arriving residents reuse an existing id")
    result["facts"] = [normalize_fact(f, i, known | newcomer_ids, ticks) for i, f in enumerate(facts)]
    fact_ids = [f["id"] for f in result["facts"]]
    need(len(set(fact_ids)) == len(fact_ids), "facts", "duplicate fact ids")
    result["story"] = story
    collapse = recipe.get("collapse", {})
    need(isinstance(collapse, dict) and set(collapse) <= set(COLLAPSE), "collapse", "unknown collapse rule")
    result["collapse"] = {key: collapse.get(key, spec["default"]) for key, spec in COLLAPSE.items()}
    for key, item in result["collapse"].items():
        if item is None:
            continue
        if key == "commons":
            need(type(item) is bool, "collapse.commons", "true or false required")
        elif key.endswith("weeks"):
            integer(item, "collapse." + key, 1, 52)
        else:
            number(item, "collapse." + key, 0, 1)
    scoring = merged(SCORING, recipe.get("scoring"), "scoring")
    need(set(scoring["rates"]) == set(DOMAINS), "scoring.rates", "a rate for every domain D1-D8")
    for key, item in scoring["rates"].items():
        number(item, "scoring.rates." + key, 0, 1e6)
    for key in SCORING:
        if key != "rates":
            number(scoring[key], "scoring." + key, -1e4, 1e4)
    result["scoring"] = scoring
    return result


def summary(recipe):
    return dict(id=recipe["id"], name=recipe["name"], description=recipe["description"], ticks=recipe["ticks"],
                seeds=recipe["seeds"], residents=len(recipe["residents"]), facts=len(recipe["facts"]),
                nodes=len(recipe["story"]["nodes"]), actions_per_week=recipe["steward"]["actions_per_week"],
                engine="polis", decisions=recipe["ticks"] * len(recipe["seeds"]))


class PolisRecipes:
    """Bundled recipes plus authored ones in a local directory (excluded from Git)."""

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else None

    def bundled_ids(self):
        return {json.loads(p.read_text(encoding="utf8"))["id"] for p in BUNDLED.glob("*.json")}

    def all(self):
        """Bundled recipes first (the default recipe leads), then authored ones, by id."""
        found = {}
        for folder in [BUNDLED] + ([self.directory] if self.directory and self.directory.is_dir() else []):
            for path in sorted(folder.glob("*.json"), key=lambda p: (p.stem != DEFAULT, p.name)):
                try:
                    recipe = normalize(json.loads(path.read_text(encoding="utf8")))
                except (ValueError, OSError):
                    continue
                if recipe["id"] not in found:
                    found[recipe["id"]] = recipe
        return found

    def get(self, key):
        recipes = self.all()
        if key not in recipes:
            raise ValueError("Unknown society recipe: " + str(key))
        return recipes[key]

    def raw(self, key):
        for folder in [BUNDLED] + ([self.directory] if self.directory else []):
            path = folder / f"{key}.json"
            if RECIPE_IDENT.fullmatch(key) and path.is_file():
                return json.loads(path.read_text(encoding="utf8"))
        raise ValueError("Unknown society recipe: " + str(key))

    def save(self, recipe):
        if self.directory is None:
            raise ValueError("No directory for authored society recipes")
        normalized = normalize(recipe)
        if normalized["id"] in self.bundled_ids():
            raise ValueError("Bundled recipes are read-only; save under a new id")
        from ..persistence.json_files import write_json

        write_json(self.directory / f"{normalized['id']}.json", recipe)
        return normalized

    def delete(self, key):
        if key in self.bundled_ids():
            raise ValueError("Bundled recipes are read-only")
        path = self.directory / f"{key}.json" if self.directory and RECIPE_IDENT.fullmatch(key) else None
        if path is None or not path.is_file():
            raise ValueError("Unknown society recipe: " + str(key))
        path.unlink()


def catalog():
    """Everything the Society Lab needs to author a recipe."""
    return dict(
        format=FORMAT,
        traits={k: dict(default=v, help=TRAITS[k]) for k, v in TRAIT_DEFAULTS.items()},
        benefits=BENEFITS,
        archetypes=ARCHETYPES,
        roles=ROLES,
        skills=list(SKILLS),
        seasons=list(SEASONS),
        concerns=CONCERNS,
        approaches=list(APPROACHES),
        policies=POLICIES,
        projects=PROJECTS,
        belief_effects=BELIEF_EFFECTS,
        metrics=METRICS,
        effects=EFFECTS,
        collapse=COLLAPSE,
        domains=DOMAINS,
        conflict_kinds=list(CONFLICT_KINDS),
        resolutions=list(RESOLUTIONS),
        defaults=dict(world=WORLD, steward=STEWARD, scoring=SCORING, resident_state=RESIDENT_STATE,
                      merchant=MERCHANT),
    )
