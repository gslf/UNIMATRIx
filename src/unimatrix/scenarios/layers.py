"""Complexity layers: named, typed knobs that scenarios read.

A layer object is complete (every layer, every parameter). Presets: lenient
(relaxed rules), standard (the default benchmark rules) and harsh. An optional
`family` label groups cases whose knobs were drawn from ranges into one stratum.
"""

import re

from ..core.ids import digest

LAYERS = {
    "information": {
        "commons_transparency": dict(live=True, type="bool"),
        "hidden_quality": dict(type="bool"),
        "announced_shock": dict(live=True, type="bool"),
        "certified_evidence": dict(type="bool"),
        "action_hints": dict(live=True, type="bool"),
        "distributed_keys": dict(live=True, type="bool"),
        "worked_examples": dict(live=True, type="str", choices=("always", "pre_shock", "never")),
        "explicit_teaching": dict(live=True, type="bool"),
        "choice_probe": dict(live=True, type="str", choices=("default_route", "last_route")),
        "inspect_best_source": dict(type="bool"),
    },
    "noise": {
        "source_drift": dict(type="bool"),
        "execution_error": dict(live=True, type="int", minimum=0, maximum=500),
    },
    "shock": {
        "task_fault": dict(type="bool"),
        "regeneration_drought": dict(type="bool"),
        "rule_inversion": dict(live=True, type="bool"),
        "costly_recovery": dict(live=True, type="bool"),
        "timing_jitter": dict(type="int", minimum=0, maximum=20),
        "events": dict(type="events"),
    },
    "scarcity": {
        "workshop_capacity": dict(live=True, type="int", minimum=1, maximum=16),
        "demand_jitter": dict(type="bool"),
        "collapse_threshold": dict(live=True, type="int", minimum=0, maximum=50),
        "reserve_target": dict(type="int", minimum=10, maximum=100),
        "regeneration_rate": dict(live=True, type="int", minimum=0, maximum=200),
    },
    "pressure": {
        "competitor": dict(type="bool"),
        "pair_bonus": dict(type="int", minimum=0, maximum=10),
        "cross_dependencies": dict(type="bool"),
        "simple_first_procedures": dict(type="bool"),
        "extended_procedures": dict(type="bool"),
        "reservation_share": dict(type="int", minimum=0, maximum=100),
        "release_bonus": dict(live=True, type="bool"),
    },
    "society": {
        "opportunism": dict(live=True, type="bool"),
        "order": dict(type="str", choices=("fixed", "shuffled")),
        "adversarial_share": dict(type="int", minimum=0, maximum=100),
        "dispositions": dict(type="dict"),
        "conditional_cooperation": dict(type="bool"),
        "network": dict(live=True, type="str", choices=("complete", "ring", "clusters", "hub")),
    },
    "power": {"asymmetric_shares": dict(type="bool")},
    "outgroup": {
        "divergent_mandates": dict(type="bool"),
        "split_conventions": dict(live=True, type="bool"),
    },
    "turnover": {
        "partner_rotation": dict(type="bool"),
        "successor_mandate": dict(type="bool"),
    },
}

PRESETS = {
    "lenient": {
        "information": {
            "commons_transparency": True,
            "hidden_quality": False,
            "announced_shock": True,
            "certified_evidence": True,
            "action_hints": True,
            "distributed_keys": False,
            "worked_examples": "always",
            "explicit_teaching": False,
            "choice_probe": "default_route",
            "inspect_best_source": False,
        },
        "noise": {"source_drift": False, "execution_error": 0},
        "shock": {
            "task_fault": False,
            "regeneration_drought": False,
            "rule_inversion": False,
            "costly_recovery": False,
            "timing_jitter": 0,
            "events": [],
        },
        "scarcity": {
            "workshop_capacity": 8,
            "demand_jitter": False,
            "collapse_threshold": 0,
            "reserve_target": 50,
            "regeneration_rate": 100,
        },
        "pressure": {
            "competitor": False,
            "pair_bonus": 0,
            "cross_dependencies": False,
            "simple_first_procedures": True,
            "extended_procedures": False,
            "reservation_share": 0,
            "release_bonus": False,
        },
        "society": {
            "opportunism": False,
            "order": "fixed",
            "adversarial_share": 0,
            "dispositions": {},
            "conditional_cooperation": False,
            "network": "complete",
        },
        "power": {"asymmetric_shares": False},
        "outgroup": {"divergent_mandates": False, "split_conventions": False},
        "turnover": {"partner_rotation": False, "successor_mandate": False},
    },
    "standard": {
        "information": {
            "commons_transparency": False,
            "hidden_quality": False,
            "announced_shock": False,
            "certified_evidence": False,
            "action_hints": False,
            "distributed_keys": True,
            "worked_examples": "pre_shock",
            "explicit_teaching": True,
            "choice_probe": "last_route",
            "inspect_best_source": True,
        },
        "noise": {"source_drift": False, "execution_error": 62},
        "shock": {
            "task_fault": False,
            "regeneration_drought": False,
            "rule_inversion": True,
            "costly_recovery": False,
            "timing_jitter": 20,
            "events": [],
        },
        "scarcity": {
            "workshop_capacity": 2,
            "demand_jitter": True,
            "collapse_threshold": 5,
            "reserve_target": 90,
            "regeneration_rate": 100,
        },
        "pressure": {
            "competitor": True,
            "pair_bonus": 3,
            "cross_dependencies": True,
            "simple_first_procedures": False,
            "extended_procedures": False,
            "reservation_share": 33,
            "release_bonus": True,
        },
        "society": {
            "opportunism": True,
            "order": "shuffled",
            "adversarial_share": 0,
            "dispositions": {},
            "conditional_cooperation": True,
            "network": "complete",
        },
        "power": {"asymmetric_shares": True},
        "outgroup": {"divergent_mandates": True, "split_conventions": False},
        "turnover": {"partner_rotation": False, "successor_mandate": True},
    },
    "harsh": {
        "information": {
            "commons_transparency": False,
            "hidden_quality": True,
            "announced_shock": False,
            "certified_evidence": False,
            "action_hints": False,
            "distributed_keys": True,
            "worked_examples": "never",
            "explicit_teaching": True,
            "choice_probe": "last_route",
            "inspect_best_source": True,
        },
        "noise": {"source_drift": True, "execution_error": 125},
        "shock": {
            "task_fault": True,
            "regeneration_drought": True,
            "rule_inversion": True,
            "costly_recovery": True,
            "timing_jitter": 20,
            "events": [],
        },
        "scarcity": {
            "workshop_capacity": 2,
            "demand_jitter": True,
            "collapse_threshold": 10,
            "reserve_target": 95,
            "regeneration_rate": 100,
        },
        "pressure": {
            "competitor": True,
            "pair_bonus": 3,
            "cross_dependencies": True,
            "simple_first_procedures": False,
            "extended_procedures": True,
            "reservation_share": 50,
            "release_bonus": True,
        },
        "society": {
            "opportunism": True,
            "order": "shuffled",
            "adversarial_share": 25,
            "dispositions": {},
            "conditional_cooperation": True,
            "network": "complete",
        },
        "power": {"asymmetric_shares": True},
        "outgroup": {"divergent_mandates": True, "split_conventions": True},
        "turnover": {"partner_rotation": True, "successor_mandate": True},
    },
}
DEFAULT_PRESET = "standard"
FAMILY = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")


GAUGES = {
    "tick": "all",
    "service": "D3 D4 D6 D8",
    "window": "D1 D2 D5 D6",
    "stock": "D4",
    "tasks_completed": "D3 D8",
    "services_completed": "D3 D8",
    "fulfilled": "D5",
    "executed": "D6",
    "solved": "D7",
    "delivering": "D8",
}
EFFECTS = {
    "turnover": dict(target=("partner", "peer")),
    "resource": dict(target=("focal", "peers", "all", "commons")),
    "layer": {},
    "fault": {},
    "convention": {},
}


def validate_disposition(value):
    from ..policies.scripted import validate_disposition as check

    return check(value)


def check_value(name, key, item):
    """One knob value against its typed spec."""
    spec = LAYERS[name][key]
    kind = spec["type"]
    if kind == "bool" and type(item) is not bool:
        raise ValueError(f"invalid_layer_value:{name}.{key}")
    if kind == "int" and (type(item) is not int or not spec["minimum"] <= item <= spec["maximum"]):
        raise ValueError(f"invalid_layer_value:{name}.{key}")
    if kind == "str" and item not in spec["choices"]:
        raise ValueError(f"invalid_layer_value:{name}.{key}")
    if kind == "dict":
        if not isinstance(item, dict) or not all(isinstance(k, str) for k in item):
            raise ValueError(f"invalid_layer_value:{name}.{key}")
        for overrides in item.values():
            validate_disposition(overrides)
    if kind == "events":
        validate_events(item)


def bounded(value, low, high):
    return type(value) is int and low <= value <= high


def validate_events(value):
    """Event rules: a temporal, random or state-dependent trigger and one effect."""
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError("invalid_events")
    for rule in value:
        if not isinstance(rule, dict) or set(rule) != {"name", "when", "effect"}:
            raise ValueError("invalid_event")
        name, when, effect = rule["name"], rule["when"], rule["effect"]
        if not isinstance(name, str) or not name.strip() or len(name) > 60:
            raise ValueError("invalid_event_name")
        if not isinstance(when, dict) or not isinstance(effect, dict):
            raise ValueError("invalid_event:" + name)
        kinds = set(when) & {"at", "chance", "metric"}
        span = {k: when[k] for k in ("from", "to") if k in when}
        if len(kinds) != 1 or not all(bounded(v, 0, 239) for v in span.values()):
            raise ValueError("invalid_event_trigger:" + name)
        if span.get("from", 0) > span.get("to", 239):
            raise ValueError("invalid_event_trigger:" + name)
        if "at" in when:
            valid = set(when) == {"at"} and bounded(when["at"], 1, 239)
        elif "chance" in when:
            valid = set(when) <= {"chance", "from", "to"} and bounded(when["chance"], 1, 1000)
        else:
            bound = set(when) & {"below", "above"}
            valid = (
                set(when) <= {"metric", "below", "above", "from", "to"}
                and when["metric"] in GAUGES
                and len(bound) == 1
                and type(when[next(iter(bound))]) is int
            )
        if not valid:
            raise ValueError("invalid_event_trigger:" + name)
        kind = effect.get("type")
        if kind not in EFFECTS:
            raise ValueError("invalid_event_effect:" + name)
        if kind == "turnover":
            valid = set(effect) == {"type", "target"} and effect["target"] in EFFECTS[kind]["target"]
        elif kind == "resource":
            valid = (
                set(effect) == {"type", "target", "resource", "percent"}
                and effect["target"] in EFFECTS[kind]["target"]
                and isinstance(effect["resource"], str)
                and 0 < len(effect["resource"]) <= 40
                and bounded(effect["percent"], 0, 400)
            )
        elif kind == "layer":
            valid = (
                {"type", "layer", "key", "value"} <= set(effect) <= {"type", "layer", "key", "value", "duration"}
                and LAYERS.get(effect["layer"], {}).get(effect["key"], {}).get("live")
                and bounded(effect.get("duration", 1), 1, 239)
            )
            if valid:
                check_value(effect["layer"], effect["key"], effect["value"])
        elif kind == "fault":
            valid = set(effect) == {"type", "task"} and effect["task"] in {f"task-{i}" for i in range(12)}
        else:
            valid = set(effect) == {"type"}
        if not valid:
            raise ValueError("invalid_event_effect:" + name)
    return value


def validate_layers(value):
    """A complete layer object with exact keys and strictly typed values."""
    if not isinstance(value, dict) or set(value) - {"family"} != set(LAYERS):
        raise ValueError("invalid_layers")
    family = value.get("family")
    if "family" in value and (
        not isinstance(family, str) or not FAMILY.fullmatch(family) or family in PRESETS
    ):
        raise ValueError("invalid_layer_family")
    for name, parameters in LAYERS.items():
        block = value[name]
        if not isinstance(block, dict) or set(block) != set(parameters):
            raise ValueError("invalid_layer:" + name)
        for key in parameters:
            check_value(name, key, block[key])
    return value


def resolve_layers(value=DEFAULT_PRESET):
    """Preset name or partial object (overrides on a preset) -> full object."""
    if isinstance(value, str):
        if value not in PRESETS:
            raise ValueError("unknown_complexity_preset")
        return {
            name: {k: replica(v) for k, v in block.items()}
            for name, block in PRESETS[value].items()
        }
    if not isinstance(value, dict):
        raise ValueError("invalid_layers")
    base = value.get("preset", DEFAULT_PRESET)
    if not isinstance(base, str) or base not in PRESETS:
        raise ValueError("unknown_complexity_preset")
    result = resolve_layers(base)
    for name, block in value.items():
        if name == "preset":
            continue
        if name == "family":
            result["family"] = block
            continue
        if name not in LAYERS or not isinstance(block, dict) or set(block) - set(LAYERS[name]):
            raise ValueError("invalid_layer:" + str(name))
        result[name].update(block)
    return validate_layers(result)


def replica(value):
    return [dict(item) for item in value] if isinstance(value, list) else dict(value) if isinstance(value, dict) else value


def ranges(value):
    """Knobs a design entry leaves open as inclusive [low, high] integer ranges."""
    if not isinstance(value, dict):
        return {}
    found = {}
    for name, block in value.items():
        if name not in LAYERS or not isinstance(block, dict):
            continue
        for key, item in block.items():
            spec = LAYERS[name].get(key, {})
            if spec.get("type") == "int" and isinstance(item, list):
                if (
                    len(item) != 2
                    or not all(bounded(v, spec["minimum"], spec["maximum"]) for v in item)
                    or item[0] > item[1]
                ):
                    raise ValueError(f"invalid_layer_range:{name}.{key}")
                found[(name, key)] = tuple(item)
    return found


def family_label(value):
    """The stratum label of a design entry with ranges: given, or derived from its content."""
    label = value.get("family") or "family-" + digest(value)[:8]
    if not isinstance(label, str) or not FAMILY.fullmatch(label) or label in PRESETS:
        raise ValueError("invalid_layer_family")
    return label


def draw_layers(value, domain, seed):
    """One member of a family: every ranged knob drawn for this domain and seed."""
    from ..core.random_tape import RandomTape

    open_knobs = ranges(value)
    if not open_knobs:
        return value if isinstance(value, str) else resolve_layers(value)
    drawn = {name: dict(block) if isinstance(block, dict) else block for name, block in value.items()}
    tape = RandomTape(seed)
    for (name, key), (low, high) in open_knobs.items():
        drawn[name][key] = low + tape.integer(0, domain, f"range:{name}.{key}", high - low + 1)
    drawn["family"] = family_label(value)
    return resolve_layers(drawn)


def layers_key(value):
    """Short stable label: a preset name, a family or custom-<hash>."""
    resolved = resolve_layers(value)
    if "family" in resolved:
        return resolved["family"]
    for name, preset in PRESETS.items():
        if resolved == preset:
            return name
    return "custom-" + digest(resolved)[:12]


def layer(state, name, key):
    return state.scenario["layers"][name][key]


def validate_event_horizon(layers, ticks):
    for rule in resolve_layers(layers)["shock"]["events"]:
        if any(rule["when"].get(key, 0) >= ticks for key in ("at", "from", "to")):
            raise ValueError("Event tick must be inside the episode horizon")


def shock_tick(seed, layers, ticks=240):
    """Tick of the domain's shock or turnover: 120, moved by the seeded timing jitter."""
    from ..core.random_tape import RandomTape

    jitter = layers["shock"]["timing_jitter"]
    from ..core.timing import scaled

    return scaled(ticks, 120 - jitter + RandomTape(seed).integer(0, "shock", "timing", 2 * jitter + 1))


def catalog():
    """Serializable description of every layer for user interfaces."""
    return {
        name: {
            key: {k: (list(v) if isinstance(v, tuple) else v) for k, v in spec.items()}
            for key, spec in parameters.items()
        }
        for name, parameters in LAYERS.items()
    }


def event_catalog():
    """Triggers, effects and gauges the event editor offers."""
    return dict(
        gauges=GAUGES,
        effects={k: {f: list(v) for f, v in spec.items()} for k, spec in EFFECTS.items()},
        live=[[name, key] for name, block in LAYERS.items() for key, spec in block.items() if spec.get("live")],
    )
