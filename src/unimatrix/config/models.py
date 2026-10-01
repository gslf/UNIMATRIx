"""Configuration for the unified runtime. Unknown fields are errors."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["unimatrix.run.v4"] = "unimatrix.run.v4"
    mode: Literal["core", "explore", "society"]
    domain: Literal["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "social"]
    seed: int = 0
    ticks: int = Field(240, ge=1, le=1000000)
    layers: str | dict = "standard"
    role: Literal["advantaged", "disadvantaged"] = "advantaged"
    candidate: str | dict = "reciprocal"
    policies: list[str | dict] | None = None
    population: int = Field(8, ge=2, le=128)
    capacity: int = Field(8, ge=2, le=128)
    generation_tokens_per_tick: int = Field(32768, ge=256, le=65536)

    @model_validator(mode="after")
    def validate_shape(self):
        from ..benchmark.validation import validate_policy
        from ..scenarios.layers import resolve_layers

        resolve_layers(self.layers)
        if self.population > self.capacity:
            raise ValueError("population exceeds capacity")
        if self.mode == "core" and (
            self.domain == "social"
            or self.ticks not in (72, 240)
            or self.population != self.capacity
            or self.policies
            or self.generation_tokens_per_tick != 32768
        ):
            raise ValueError(
                "Core requires a benchmark domain, 72 or 240 ticks and a fully populated society"
            )
        if self.domain != "social" and (
            self.ticks not in (72, 240) or self.population != self.capacity or self.capacity < 8
        ):
            raise ValueError("Benchmark scenarios require 72 or 240 ticks and 8–128 active agents")
        if self.policies and len(self.policies) != self.capacity:
            raise ValueError("One policy per slot is required, including vacant slots")
        for policy in [self.candidate, *(self.policies or [])]:
            validate_policy(policy)
        return self

    def manifest(self):
        from ..benchmark.manifests import episode
        from ..core.ids import digest

        manifest = episode(
            self.domain,
            seed=self.seed,
            role=self.role,
            candidate=self.candidate,
            peer_count=max(7, self.capacity - 1),
            layers=self.layers,
        )
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
