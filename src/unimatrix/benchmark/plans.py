"""Read author-owned benchmark JSON files and bind a candidate to their fixed cases."""

import json
import math
import re
from copy import deepcopy
from pathlib import Path

from ..core.ids import digest
from .fingerprints import runtime_fingerprint
from .manifests import FIELDS, episode
from .validation import validate_manifest, validate_policy

BUNDLED = Path(__file__).with_name("plans")
DEFAULT_PLAN = "standard-v1"
BUDGETS = dict(
    generation_tokens_per_decision=None,
    observation_bytes=24000,
    envelope_bytes=6144,
    attempts_per_decision=3,
)


def validate_plan(spec):
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
    if not isinstance(spec, dict) or set(spec) != required:
        raise ValueError("A plan must contain exactly: " + ", ".join(sorted(required)))
    if spec["format"] != "unimatrix.benchmark-plan.v1" or spec["engine"] != "unimatrix-3":
        raise ValueError("Unsupported plan format or engine")
    if not isinstance(spec["id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", spec["id"]):
        raise ValueError("Invalid plan ID")
    if not all(isinstance(spec[k], str) and spec[k].strip() for k in ("name", "description")):
        raise ValueError("Plan name and description are required")
    if (
        type(spec["ticks"]) is not int
        or spec["ticks"] != 240
        or spec["budgets"] not in (BUDGETS, dict(BUDGETS, generation_tokens_per_decision=4096))
    ):
        raise ValueError("This engine requires 240 ticks and the supported fixed decision budgets")
    if not isinstance(spec["peers"], list) or not 7 <= len(spec["peers"]) <= 127:
        raise ValueError("Choose between 7 and 127 peer policies")
    for peer in spec["peers"]:
        validate_policy(peer)
    domains = spec["domains"]
    if (
        not isinstance(domains, dict)
        or not domains
        or set(domains) - {f"D{i}" for i in range(1, 9)}
    ):
        raise ValueError("Plan domains must be selected from D1–D8")
    catalog = json.loads((BUNDLED.parent / "core-v1.json").read_text())["domains"]
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
        raise ValueError("The plan must contain an explicit list of cases")
    seen = set()
    for case in spec["cases"]:
        if not isinstance(case, dict) or set(case) != set(FIELDS):
            raise ValueError("Each case requires domain, level, seed, role and replicate")
        if (
            case["domain"] not in domains
            or case["role"] not in {"advantaged", "disadvantaged"}
            or any(type(case[k]) is not int for k in ("level", "seed", "replicate"))
            or case["level"] not in (1, 2, 3)
            or case["replicate"] < 0
        ):
            raise ValueError("Invalid benchmark case")
        key = tuple(case[k] for k in FIELDS)
        if key in seen:
            raise ValueError("Duplicate benchmark case")
        seen.add(key)
    if {case["domain"] for case in spec["cases"]} != set(domains):
        raise ValueError("Each scored domain must have cases")
    return spec


class PlanRepository:
    def __init__(self, directory=None, default=DEFAULT_PLAN):
        self.directory = Path(directory) if directory else None
        self.default = default

    def all(self):
        plans = {}
        for folder in [BUNDLED] + ([self.directory] if self.directory else []):
            local_ids = set()
            for file in sorted(folder.glob("*.json")):
                spec = validate_plan(json.loads(file.read_text()))
                if spec["id"] in local_ids:
                    raise ValueError("Duplicate plan ID in " + str(folder))
                local_ids.add(spec["id"])
                plans[spec["id"]] = spec
        if self.default not in plans:
            raise ValueError("Default benchmark plan not found: " + self.default)
        return plans

    def get(self, ident):
        try:
            return self.all()[ident]
        except KeyError as error:
            raise ValueError("Unknown benchmark plan") from error


def bind_candidate(spec, candidate):
    """Instantiate only the cases already written in JSON; never choose new cases."""
    validate_plan(spec)
    validate_policy(candidate)
    spec = deepcopy(spec)
    if any(c["level"] != 2 for c in spec["cases"]):
        raise ValueError("This recipe uses retired difficulty settings; rebuild it in the Lab")
    scoring = scoring_spec(spec)
    pool = next(iter(scoring["population_by_seed"].values()))
    manifests = []
    for case in spec["cases"]:
        item = episode(
            **case,
            candidate=candidate,
            population=pool,
            suite_hash=digest(scoring),
            peer_count=len(spec["peers"]),
        )
        peers = [s for s in item["slots"] if s != item["focal_slot"]]
        for slot, config in zip(peers, spec["peers"]):
            item["policies"][slot] = deepcopy(config)
        item["run_id"] = digest({k: v for k, v in item.items() if k != "run_id"})[:24]
        validate_manifest(item)
        manifests.append(item)
    identity = candidate
    if isinstance(candidate, dict):
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
                "max_input_tokens",
            }
        }
    return dict(suite=scoring, candidate_id=digest(identity), episodes=manifests)


def scoring_spec(spec):
    pool = "P1" if any(isinstance(p, dict) for p in spec["peers"]) else "P0"
    return dict(
        spec,
        budgets=deepcopy(BUDGETS),
        benchmark_id=spec["id"],
        scaffold_id=spec["engine"],
        release_status="draft",
        runtime_fingerprint=runtime_fingerprint(),
        population_by_seed={str(c["seed"]): pool for c in spec["cases"]},
    )
