"""Build balanced explicit case lists from an operator's research design."""

import itertools
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..benchmark.plans import BUDGETS, BUNDLED, validate_plan
from ..core.ids import digest
from ..persistence.json_files import read_json


class Design(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_plan: str = "compact-v1"
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,69}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    domains: list[str] = Field(min_length=1, max_length=8)
    levels: list[int] = Field(default_factory=lambda: [2], exclude=True)
    seeds: list[int] = Field(min_length=1, max_length=32)
    holdout_seeds: list[int] = Field(default_factory=list, max_length=32)
    roles: list[str] = Field(min_length=1, max_length=2)
    replicates: int = Field(1, ge=1, le=4)
    peers: list[str | dict] | None = Field(default=None, min_length=7, max_length=127)
    weights: dict[str, dict[str, float]] | None = None

    @model_validator(mode="after")
    def axes(self):
        for key in ["domains", "levels", "seeds", "holdout_seeds", "roles"]:
            values = getattr(self, key)
            if len(values) != len(set(values)):
                raise ValueError("Duplicate values in " + key)
        if set(self.domains) - {f"D{i}" for i in range(1, 9)}:
            raise ValueError("Choose domains D1–D8")
        if self.levels != [2]:
            raise ValueError("Difficulty selection has been retired; rebuild with the fixed rules")
        if set(self.roles) - {"advantaged", "disadvantaged"}:
            raise ValueError("Unknown starting role")
        if set(self.seeds) & set(self.holdout_seeds):
            raise ValueError("Development and holdout seeds must not overlap")
        if not self.name.strip() or not self.description.strip():
            raise ValueError("A name and description are required")
        return self


def build_design(design, plans):
    base = plans.get(design.base_plan)
    catalog = read_json(BUNDLED.parent / "core-v1.json")["domains"]
    if design.weights and set(design.weights) - set(design.domains):
        raise ValueError("Metric weights must refer to selected domains")

    def build(seeds, holdout=False):
        result = deepcopy(base)
        result.update(
            budgets=deepcopy(BUDGETS),
            id=design.id + ("-holdout" if holdout else ""),
            name=design.name + (" · Holdout" if holdout else ""),
            description=design.description,
            peers=deepcopy(base["peers"] if design.peers is None else design.peers),
            domains={d: deepcopy(base["domains"].get(d, catalog[d])) for d in design.domains},
            cases=[
                dict(zip(["domain", "level", "seed", "role", "replicate"], values))
                for values in itertools.product(
                    design.domains,
                    [2],
                    sorted(seeds),
                    design.roles,
                    range(design.replicates),
                )
            ],
        )
        for domain, weights in (design.weights or {}).items():
            result["domains"][domain]["metrics"] = weights
        return validate_plan(result)

    spec = build(design.seeds)
    holdout = build(design.holdout_seeds, True) if design.holdout_seeds else None
    notes = []
    if len(design.seeds) < 4:
        notes.append("Use at least four development seeds to assess variation across instances.")
    if len(design.roles) < 2:
        notes.append("Only one starting role is covered; role asymmetry will not be measured.")
    if not holdout:
        notes.append("Reserve disjoint holdout seeds before using results to tune this plan.")
    if len(design.domains) < 8:
        notes.append("The score covers only the selected domains.")
    notes.append("Engine budgets are fixed. Research findings do not automatically certify a plan.")
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
