"""Configuration for the unified runtime. Unknown and retired fields are errors."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["unimatrix.run.v3"] = "unimatrix.run.v3"
    mode: Literal["core", "explore", "society"]
    domain: Literal["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "social"]
    seed: int = 0
    ticks: int = Field(240, ge=1, le=1000000)
    level: int = Field(1, ge=1, le=3)
    role: Literal["advantaged", "disadvantaged"] = "advantaged"
    candidate: str | dict = "reciprocal"
    policies: list[str | dict] | None = None
    population: int = Field(8, ge=2, le=16)
    capacity: int = Field(8, ge=2, le=16)
    generation_tokens_per_tick: int = Field(32768, ge=256, le=65536)

    @model_validator(mode="after")
    def validate_shape(self):
        from ..benchmark.validation import validate_policy

        if self.population > self.capacity:
            raise ValueError("population exceeds capacity")
        if self.mode == "core" and (
            self.domain == "social"
            or self.ticks != 240
            or self.population != 8
            or self.capacity != 8
            or self.policies
            or self.generation_tokens_per_tick != 32768
        ):
            raise ValueError("Core requires a benchmark domain, 240 ticks and eight fixed slots")
        if self.domain != "social" and (
            self.ticks != 240 or self.capacity != 8 or self.population != 8
        ):
            raise ValueError("Benchmark scenarios require 240 ticks and eight slots")
        if self.policies and len(self.policies) != self.capacity:
            raise ValueError("One policy per slot is required, including vacant slots")
        for policy in [self.candidate, *(self.policies or [])]:
            validate_policy(policy)
        return self

    def manifest(self):
        from ..benchmark.manifests import episode
        from ..core.ids import digest

        manifest = episode(self.domain, self.level, self.seed, self.role, candidate=self.candidate)
        manifest.update(mode=self.mode, ticks=self.ticks)
        if self.mode != "core":
            slots = [f"slot-{i}" for i in range(self.capacity)]
            manifest.update(
                slots=slots,
                focal_slot=slots[0],
                population=self.mode,
                initial_population=self.population,
                generation_tokens_per_tick=self.generation_tokens_per_tick,
                policies=dict(zip(slots, self.policies or [self.candidate] * self.capacity)),
            )
        manifest["run_id"] = digest({k: v for k, v in manifest.items() if k != "run_id"})[:24]
        return manifest


def load_config(path):
    return Config.model_validate(json.loads(Path(path).read_text()))
