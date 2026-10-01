"""Validate recipes and bind candidates to fixed benchmark cases."""

import json
import math
import re
from copy import deepcopy
from pathlib import Path

from ..core.ids import digest
from ..scenarios.layers import layers_key
from .fingerprints import runtime_fingerprint
from .manifests import FIELDS, episode, peer_slots
from .validation import is_model, validate_manifest, validate_policy

BUNDLED = Path(__file__).with_name("plans")
FORMAT = "unimatrix.benchmark-recipe.v1"
DEFAULT_RECIPE = "standard-v1"
BUDGETS = dict(
    generation_tokens_per_decision=None,
    observation_bytes=24000,
    envelope_bytes=6144,
    attempts_per_decision=3,
)


def validate_recipe(spec):
    required = {
        "format",
        "id",
        "name",
        "description",
        "engine",
        "ticks",
        "peers",
        "cases",
        "domains",
        "budgets",
        "bootstrap",
    }
    if not isinstance(spec, dict) or not required <= set(spec) <= required | {"candidate_profiles", "max_wall_seconds"}:
        raise ValueError("A benchmark recipe must contain exactly: " + ", ".join(sorted(required)))
    if "max_wall_seconds" in spec and (
        type(spec["max_wall_seconds"]) not in (int, float)
        or not math.isfinite(spec["max_wall_seconds"])
        or not 0 < spec["max_wall_seconds"] <= 52 * 3600
    ):
        raise ValueError("Recipe wall-time budget must be positive and at most 52 hours")
    from .personas import validate_profiles

    validate_profiles(spec.get("candidate_profiles", {}))
    if spec["format"] != FORMAT or spec["engine"] != "unimatrix-4":
        raise ValueError("Unsupported benchmark recipe format or engine")
    if not isinstance(spec["id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", spec["id"]):
        raise ValueError("Invalid benchmark recipe ID")
    if not all(isinstance(spec[k], str) and spec[k].strip() for k in ("name", "description")):
        raise ValueError("Benchmark recipe name and description are required")
    if (
        type(spec["ticks"]) is not int
        or spec["ticks"] not in (72, 240)
        or spec["budgets"] != BUDGETS
    ):
        raise ValueError("This engine requires 72 or 240 ticks and the supported fixed decision budgets")
    if not isinstance(spec["peers"], list) or not 7 <= len(spec["peers"]) <= 127:
        raise ValueError("Choose between 7 and 127 peer policies")
    for peer in spec["peers"]:
        if peer == "oracle":
            raise ValueError("The oracle is a research reference, not a peer")
        validate_policy(peer)
    domains = spec["domains"]
    if (
        not isinstance(domains, dict)
        or not domains
        or set(domains) - {f"D{i}" for i in range(1, 9)}
    ):
        raise ValueError("Benchmark recipe domains must be selected from D1–D8")
    catalog = json.loads((BUNDLED.parent / "metric-catalog.json").read_text())["domains"]
    for domain, entry in domains.items():
        weights = entry.get("metrics", {})
        if (
            set(weights) != set(catalog[domain]["metrics"])
            or any(
                type(w) not in (float, int) or not math.isfinite(w) or w <= 0
                for w in weights.values()
            )
            or not math.isclose(sum(weights.values()), 1)
        ):
            raise ValueError("Invalid metric catalog or weights for " + domain)
    bootstrap = spec["bootstrap"]
    if (
        not isinstance(bootstrap, dict)
        or set(bootstrap) != {"seed", "resamples"}
        or type(bootstrap["seed"]) is not int
        or type(bootstrap["resamples"]) is not int
        or not 1 <= bootstrap["resamples"] <= 10000
    ):
        raise ValueError("Invalid bootstrap settings")
    if not isinstance(spec["cases"], list) or not spec["cases"]:
        raise ValueError("The benchmark recipe must contain an explicit list of cases")
    from ..scenarios.layers import validate_event_horizon

    seen = set()
    for case in spec["cases"]:
        if not isinstance(case, dict) or set(case) != set(FIELDS):
            raise ValueError("Each case requires domain, layers, seed, role and replicate")
        try:
            complexity = layers_key(case["layers"])
            validate_event_horizon(case["layers"], spec["ticks"])
        except ValueError as error:
            raise ValueError("Invalid benchmark case") from error
        if (
            case["domain"] not in domains
            or case["role"] not in {"advantaged", "disadvantaged"}
            or any(type(case[k]) is not int for k in ("seed", "replicate"))
            or case["replicate"] < 0
        ):
            raise ValueError("Invalid benchmark case")
        key = (case["domain"], complexity, case["seed"], case["role"], case["replicate"])
        if key in seen:
            raise ValueError("Duplicate benchmark case")
        seen.add(key)
    if {case["domain"] for case in spec["cases"]} != set(domains):
        raise ValueError("Each scored domain must have cases")
    return spec


class RecipeRepository:
    def __init__(self, directory=None, default=DEFAULT_RECIPE):
        self.directory = Path(directory) if directory else None
        self.default = default

    def all(self):
        plans = {}
        for folder in [BUNDLED] + ([self.directory] if self.directory else []):
            local_ids = set()
            for file in sorted(folder.glob("*.json")):
                spec = validate_recipe(json.loads(file.read_text()))
                if spec["id"] in local_ids or spec["id"] in plans:
                    raise ValueError("Duplicate benchmark recipe ID: " + spec["id"])
                local_ids.add(spec["id"])
                plans[spec["id"]] = spec
        if self.default not in plans:
            raise ValueError("Default benchmark recipe not found: " + self.default)
        return plans

    def get(self, ident):
        try:
            return self.all()[ident]
        except KeyError as error:
            raise ValueError("Unknown benchmark recipe") from error


def bind_candidate(spec, candidate):
    """Instantiate only the cases already written in JSON; never choose new cases."""
    spec = deepcopy(validate_recipe(spec))
    validate_policy(candidate)
    scoring = scoring_spec(spec)
    pool = next(iter(scoring["population_by_seed"].values()))
    manifests = []
    for case in spec["cases"]:
        item = episode(
            **case,
            ticks=spec["ticks"],
            candidate=candidate,
            population=pool,
            suite_hash=digest(scoring),
            peer_count=len(spec["peers"]),
        )
        for slot, config in zip(peer_slots(item), spec["peers"]):
            item["policies"][slot] = deepcopy(config)
        item["run_id"] = digest({k: v for k, v in item.items() if k != "run_id"})[:24]
        validate_manifest(item)
        manifests.append(item)
    identity = candidate
    if is_model(candidate):
        identity = {
            k: v
            for k, v in candidate.items()
            if k
            not in {
                "endpoint",
                "api_key_file",
                "api_key_env",
                "input_price_per_million",
                "output_price_per_million",
            }
        }
    return dict(suite=scoring, candidate_id=digest(identity), episodes=manifests)


def scoring_spec(spec):
    pool = "P1" if any(is_model(p) for p in spec["peers"]) else "P0"
    return dict(
        spec,
        budgets=deepcopy(BUDGETS),
        benchmark_id=spec["id"],
        scaffold_id=spec["engine"],
        release_status="released",
        runtime_fingerprint=runtime_fingerprint(),
        population_by_seed={str(c["seed"]): pool for c in spec["cases"]},
    )


def recipe_from_suite(suite):
    """Recover the frozen author specification; never consult a mutable catalog."""
    fields = {"format", "id", "name", "description", "engine", "ticks", "peers",
              "cases", "domains", "budgets", "bootstrap", "candidate_profiles", "max_wall_seconds"}
    return validate_recipe(deepcopy({k: v for k, v in suite.items() if k in fields}))
