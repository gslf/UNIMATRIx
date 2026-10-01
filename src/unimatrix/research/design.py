"""Build balanced explicit case lists from an operator's research design."""

import itertools
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..benchmark.recipes import BUDGETS, BUNDLED, validate_recipe
from ..core.ids import digest
from ..persistence.json_files import read_json
from ..scenarios.layers import draw_layers, family_label, layers_key, ranges


class Design(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_recipe: str = "standard-v1"
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,69}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    domains: list[str] = Field(min_length=1, max_length=8)
    complexity: list[str | dict] = Field(
        default_factory=lambda: ["standard"], min_length=1, max_length=3
    )
    seeds: list[int] = Field(min_length=1, max_length=4096)
    holdout_seeds: list[int] = Field(default_factory=list, max_length=4096)
    roles: list[str] = Field(min_length=1, max_length=2)
    replicates: int = Field(1, ge=1, le=4)
    peers: list[str | dict] | None = Field(default=None, min_length=7, max_length=127)
    holdout_peers: list[str | dict] | None = Field(default=None, min_length=7, max_length=127)
    holdout_complexity: list[str | dict] | None = Field(default=None, min_length=1, max_length=3)
    weights: dict[str, dict[str, float]] | None = None
    candidate_profiles: dict | None = None
    domain_complexity: dict[str, list[str | dict]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def axes(self):
        for key in ["domains", "seeds", "holdout_seeds", "roles"]:
            values = getattr(self, key)
            if len(values) != len(set(values)):
                raise ValueError("Duplicate values in " + key)
        if set(self.domains) - {f"D{i}" for i in range(1, 9)}:
            raise ValueError("Choose domains D1–D8")

        labels = [
            family_label(entry) if ranges(entry) else layers_key(entry) for entry in self.complexity
        ]
        if len(labels) != len(set(labels)):
            raise ValueError("Duplicate values in complexity")
        if set(self.domain_complexity) - set(self.domains):
            raise ValueError("Domain-specific conditions must refer to selected domains")
        for entries in self.domain_complexity.values():
            labels = [family_label(e) if ranges(e) else layers_key(e) for e in entries]
            if not 1 <= len(entries) <= 3 or len(labels) != len(set(labels)):
                raise ValueError("Use one to three distinct conditions per domain")
        if set(self.roles) - {"advantaged", "disadvantaged"}:
            raise ValueError("Unknown starting role")
        if any(s < 0 or s > 2**31 - 1 for s in self.seeds + self.holdout_seeds):
            raise ValueError("Seeds must be nonnegative 31-bit integers")
        if set(self.seeds) & set(self.holdout_seeds):
            raise ValueError("Development and holdout seeds must not overlap")
        if not self.name.strip() or not self.description.strip():
            raise ValueError("A name and description are required")
        return self


def build_design(design, plans):
    base = plans.get(design.base_recipe)
    catalog = read_json(BUNDLED.parent / "metric-catalog.json")["domains"]
    if design.weights and set(design.weights) - set(design.domains):
        raise ValueError("Metric weights must refer to selected domains")

    def build(seeds, holdout=False):
        result = deepcopy(base)
        result.update(
            budgets=deepcopy(BUDGETS),
            id=design.id + ("-holdout" if holdout else ""),
            name=design.name + (" · Holdout" if holdout else ""),
            description=design.description,
            peers=deepcopy(design.holdout_peers if holdout and design.holdout_peers is not None
                           else base["peers"] if design.peers is None else design.peers),
            domains={d: deepcopy(base["domains"].get(d, catalog[d])) for d in design.domains},
            cases=[
                dict(
                    domain=domain,
                    layers=draw_layers(entry, domain, seed),
                    seed=seed,
                    role=role,
                    replicate=replicate,
                )
                for domain in design.domains
                for entry, seed, role, replicate in itertools.product(
                    design.holdout_complexity if holdout and design.holdout_complexity is not None
                    else design.domain_complexity.get(domain, design.complexity),
                    sorted(seeds),
                    design.roles,
                    range(design.replicates),
                )
            ],
        )
        if design.candidate_profiles is not None:
            result["candidate_profiles"] = deepcopy(design.candidate_profiles)
        for domain, weights in (design.weights or {}).items():
            result["domains"][domain]["metrics"] = weights
        return validate_recipe(result)

    spec = build(design.seeds)
    holdout = build(design.holdout_seeds, True) if design.holdout_seeds else None
    notes = []
    if len(design.seeds) < 4:
        notes.append("Use at least four development seeds to assess variation across instances.")
    if len(design.roles) < 2:
        notes.append("Only one starting role is covered; role asymmetry will not be measured.")
    if not holdout:
        notes.append(
            "Reserve disjoint holdout seeds before using results to tune this benchmark recipe."
        )
    if len(design.domains) < 8:
        notes.append("The score covers only the selected domains.")
    if len(design.complexity) > 1:
        notes.append("Each complexity setting is a separate stratum with equal weight per domain.")
    if any(ranges(entry) for entry in design.complexity):
        notes.append(
            "Ranged knobs were drawn once per domain and seed and written into the cases; "
            "a family is one stratum."
        )
    if design.replicates > 1:
        notes.append(
            "Replicates are parallel worlds: the same instance with its own noise and priority order."
        )
    notes.append(
        "Engine budgets are fixed. Research findings do not automatically certify a benchmark recipe."
    )
    return dict(
        design=design.model_dump(),
        plan=spec,
        holdout=holdout,
        plan_hash=digest(spec),
        notes=notes,
        episodes=len(spec["cases"]),
        holdout_episodes=len(holdout["cases"]) if holdout else 0,
        candidate_decisions_max=len(spec["cases"]) * spec["ticks"],
    )
