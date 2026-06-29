"""Pydantic models for the JSON simulation configuration.

Validation is fail-fast: a malformed config produces a clear ValidationError at
load time rather than failing deep inside the orchestrator.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SimulationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    seed: int | None = 42
    tick_interval_seconds: float = Field(5.0, gt=0)
    auto_checkpoint_minutes: float = Field(5.0, gt=0)


class InferenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["vllm", "llama_cpp", "stub"] = "vllm"
    endpoint: str = "http://127.0.0.1:1234"
    model: str = "Qwen/Qwen2.5-14B-Instruct-AWQ"
    temperature: float = Field(0.95, ge=0.0, le=2.0)
    top_p: float = Field(0.95, ge=0.0, le=1.0)
    request_timeout_seconds: float = Field(600.0, gt=0)
    max_concurrent_requests: int = Field(8, ge=1)
    slow_request_warn_seconds: float = Field(30.0, gt=0)
    max_tokens_per_decision: int = Field(2000, ge=16)


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    short_term_turns: int = Field(15, ge=1)
    medium_term_summaries: int = Field(20, ge=0)
    long_term_retrieval_k: int = Field(3, ge=0)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    person_impression_update_every_n_turns: int = Field(5, ge=1)


class SocialConfig(BaseModel):
    """The connection drive — a being grows restless when unspoken-to and is
    nudged to reach out. This is NOT a status ladder; just the pull toward
    others that keeps the world from going silent."""

    model_config = ConfigDict(extra="forbid")

    social_need_initial: float = 100.0
    social_need_decay_per_tick: float = Field(8.0, ge=0.0)
    social_need_gain_per_turn: float = Field(6.0, ge=0.0)
    social_need_critical_threshold: float = 25.0
    silence_detection_seconds: float = Field(20.0, gt=0)
    forced_interaction_count_on_silence: int = Field(2, ge=1)
    max_idle_decisions_per_tick: int = Field(0, ge=0)


class MessagingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reflection_interval_ticks: int = Field(20, ge=1)
    reflection_min_new_messages: int = Field(1, ge=1)
    max_recipients_per_message: int = Field(6, ge=1)
    max_messages_per_tick: int = Field(3, ge=1)
    message_history_window: int = Field(15, ge=1)


class EcologyConfig(BaseModel):
    """The spatial substrate — the world's body, and the only mode there is.
    Beings occupy patches on a grid, perceive only what is near, must move to
    reach richer ground, and harvest resources that deplete and slowly regrow.
    None of this tells a being how to behave; it just gives the world extent, so
    that cooperation, recurring neighbours (→ bonds), and useful kin emerge from
    topology and arithmetic."""

    model_config = ConfigDict(extra="forbid")

    grid_width: int = Field(4, ge=1)
    grid_height: int = Field(4, ge=1)
    perception_radius: int = Field(1, ge=0)        # Chebyshev radius you can see
    patch_capacity: float = Field(12.0, gt=0)      # max standing resource / patch
    patch_regen_per_tick: float = Field(0.6, ge=0)
    initial_patch_fill_frac: float = Field(0.7, ge=0.0, le=1.0)
    harvest_base_yield: float = Field(3.0, ge=0)   # one solo harvest
    coharvest_bonus_per_partner: float = Field(2.0, ge=0)  # per co-present extra
    coharvest_max_partners: int = Field(3, ge=0)
    move_vitality_cost: float = Field(0.5, ge=0)   # moving tires (< work)


class WorldConfig(BaseModel):
    """The primordial substrate."""

    model_config = ConfigDict(extra="forbid")

    # --- finitude (the one hard law) ---
    vitality_initial: float = Field(100.0, gt=0)
    vitality_decay_per_tick: float = Field(1.5, ge=0)
    vitality_death_threshold: float = Field(0.0)
    # senescence: the attainable vitality ceiling falls linearly with age and
    # reaches 0 at max_age_ticks — a hard maximum lifespan, and rising survival
    # pressure as a being ages.
    max_age_ticks: int = Field(220, ge=1)
    # --- sustenance & labor (survival depends on work) ---
    sustenance_initial: float = Field(20.0, ge=0)
    sustenance_consumed_per_tick: float = Field(0.8, ge=0)
    vitality_per_sustenance: float = Field(5.0, ge=0)
    sustenance_max: float = Field(24.0, ge=0)  # storage cap; <=0 means no cap
    work_vitality_cost: float = Field(1.5, ge=0)  # labor tires — a real trade-off
    # Labor is working the land: a being harvests the patch it stands on, drawing
    # sustenance that depletes the patch and slowly regrows. Co-present harvesters
    # each take more (the co-harvest bonus, in EcologyConfig), so cooperation pays
    # by arithmetic alone — not by any rule telling beings to cooperate.
    # --- variance & mutual aid (ties become insurance) ---
    # On a "lean" tick a being's food fails to nourish it; a run of these can end
    # a loner, while those bound to others are pulled back from the edge.
    bad_streak_prob: float = Field(0.05, ge=0.0, le=1.0)
    pooling_rescue_threshold: float = Field(10.0, ge=0)
    pooling_max_transfer_per_tick: float = Field(4.0, ge=0)
    pooling_min_tie_strength: float = Field(1.0, ge=0)
    # --- reciprocity: a gift builds the tie in proportion to its size ---
    share_strength_per_unit: float = Field(0.2, ge=0)
    mutual_aid_bonus: float = Field(1.0, ge=0)
    # --- continuity: succession & heredity ---
    succession_sustenance_cost: float = Field(6.0, ge=0)
    succession_min_partner_strength: float = Field(1.0, ge=0)
    # Birth forges a naturally strong reciprocal parent<->child tie, so offspring
    # count as bonds the parent can later draw on (deferred insurance). Must be
    # >= pooling_min_tie_strength or the tie is useless for rescue.
    kin_bond_strength: float = Field(2.0, ge=0)
    # A grown, co-located child working a parent's patch lifts both their takes —
    # kin cooperation, paid through the same harvest channel as any co-presence.
    kin_coharvest_bonus: float = Field(1.5, ge=0)
    max_population: int = Field(60, ge=1)
    # a new being starts with less than a founder — reproduction is a real
    # investment — but a co-located, co-laboring child repays the lineage.
    child_sustenance_initial: float = Field(8.0, ge=0)
    maturity_age_ticks: int = Field(15, ge=0)       # below this, a successor...
    child_effort_factor: float = Field(0.5, ge=0)   # ...labors at reduced output
    # heritable traits: offspring inherit a mutated blend of both parents
    trait_mutation_sigma: float = Field(0.08, ge=0)
    trait_min: float = Field(0.5, gt=0)
    trait_max: float = Field(1.8, gt=0)
    # --- self-construction (the engine of evolution) ---
    blank_slate: bool = False  # start beings identical — a comparison control
    self_revision_interval_ticks: int = Field(15, ge=1)
    self_revision_min_new_memories: int = Field(1, ge=0)
    max_tokens_per_self_revision: int = Field(700, ge=64)
    # --- a body for the world (off by default) ---
    ecology: EcologyConfig = Field(default_factory=EcologyConfig)

    def vitality_ceiling(self, age_ticks: int) -> float:
        """Maximum vitality attainable at a given age. Falls linearly with age
        (senescence) and reaches 0 at max_age_ticks — the hard lifespan cap."""
        if self.max_age_ticks <= 0:
            return self.vitality_initial
        frac = 1.0 - max(0, age_ticks) / self.max_age_ticks
        return self.vitality_initial * max(0.0, frac)


class AgentSpec(BaseModel):
    """A being's thin seed: just enough to differ, fully overwritable."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    # Optional and free-form, not a fixed human category. Kept only for DB
    # compatibility; it is no longer shown to the being. Defaults to empty.
    gender: str = ""
    circumstance: str = ""
    disposition: str = ""


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation: SimulationConfig
    inference: InferenceConfig
    memory: MemoryConfig
    social: SocialConfig
    world: WorldConfig = Field(default_factory=WorldConfig)
    messaging: MessagingConfig = Field(default_factory=MessagingConfig)
    agents: list[AgentSpec]

    @field_validator("agents")
    @classmethod
    def _agents_unique(cls, v: list[AgentSpec]) -> list[AgentSpec]:
        ids = [a.id for a in v]
        if len(set(ids)) != len(ids):
            raise ValueError("agent ids must be unique")
        if not v:
            raise ValueError("at least one agent required")
        return v


def load_config(path: str | Path) -> Config:
    """Read a JSON config file from disk and validate it."""
    raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    return Config.model_validate(raw)
