"""Pydantic models for the JSON simulation configuration.

Validation is fail-fast: a malformed config produces a clear ValidationError at
load time rather than failing deep inside the orchestrator. Defaults match the
spec.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SimulationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    seed: int | None = 42
    language: str = "en"
    tick_interval_seconds: float = Field(5.0, gt=0)
    auto_checkpoint_minutes: float = Field(5.0, gt=0)


class InferenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["vllm", "llama_cpp", "stub"] = "vllm"
    endpoint: str = "http://localhost:8000"
    model: str = "Qwen/Qwen2.5-14B-Instruct-AWQ"
    # Cap of concurrent in-flight HTTP requests to the backend. vLLM batches
    # internally, so set this high (32–64). llama.cpp by default has a single
    # decoding slot — keep this low (4–8) or requests just queue up server-side.
    max_batch_size: int = Field(8, ge=1)
    max_tokens_per_message: int = Field(200, ge=1)
    temperature: float = Field(0.95, ge=0.0, le=2.0)
    top_p: float = Field(0.95, ge=0.0, le=1.0)
    # Per-request HTTP timeout. Big models (gpt-oss-120b on partial offload)
    # easily exceed 60s per generation, and queued requests behind them wait
    # even longer. 600s is a safe ceiling; lower for fast models.
    request_timeout_seconds: float = Field(600.0, gt=0)
    # Hard cap on output tokens for short structured outputs (decisions, votes,
    # summaries). Conversation turns use max_tokens_per_message instead.
    max_tokens_per_decision: int = Field(2000, ge=16)


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    short_term_turns: int = Field(15, ge=1)
    medium_term_summaries: int = Field(20, ge=0)
    long_term_retrieval_k: int = Field(3, ge=0)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    person_impression_update_every_n_turns: int = Field(5, ge=1)
    chroma_persist: bool = True


class SocialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    social_need_initial: float = 100.0
    social_need_decay_per_minute: float = Field(60.0, ge=0.0)
    social_need_gain_per_turn: float = Field(6.0, ge=0.0)
    social_need_critical_threshold: float = 25.0
    silence_detection_seconds: float = Field(20.0, gt=0)
    forced_interaction_count_on_silence: int = Field(2, ge=1)
    # Cap how many idle agents make a decision per tick. 0 = unlimited (the
    # spec default). Set to a smaller value (e.g. 8) when running against a
    # slow / large LLM so the inference backend isn't asked for 50 batched
    # generations every tick. Lowest social_need agents are picked first.
    max_idle_decisions_per_tick: int = Field(0, ge=0)


class ConversationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_turns_per_conversation: int = Field(30, ge=2)
    max_group_size: int = Field(6, ge=2)
    cooldown_seconds_after_end: float = Field(10.0, ge=0)
    # How many speaker turns to advance for each active conversation per tick.
    # >1 makes dialogue feel alive even with a slow tick; the inference client
    # already batches across conversations so cost scales sub-linearly.
    turns_per_tick: int = Field(2, ge=1)


class VotingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # If no vote has been proposed for this many ticks, the orchestrator picks
    # one idle agent and forces them to propose a vote on the next tick. The
    # social order doesn't change without proposals, so silence here is itself
    # a problem — keep this bound tight (≤ 10 ticks) for lively dynamics.
    max_ticks_without_vote: int = Field(10, ge=1)
    # Startup grace: for the first N ticks no vote (forced or spontaneous) may
    # happen. Lets society warm up — characters meet, opinions surface, and
    # the first vote actually comes out of something. Set 0 to disable.
    warmup_ticks: int = Field(4, ge=0)
    # Number of debate rounds run between voting.open() and collect_and_close.
    # Each round = one short speech per agent (parallel batch). 0 disables the
    # debate entirely (legacy fast-path, vote-only).
    debate_rounds: int = Field(1, ge=0)
    max_tokens_per_debate_speech: int = Field(2000, ge=10)
    # Maximum attempts (including the first) to get a well-formed vote from a
    # given agent. Anything malformed after the last attempt is recorded as a
    # 'null' vote that counts neither yes nor no.
    max_vote_attempts: int = Field(3, ge=1)


class RoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    prestige: int = Field(..., ge=0, le=100)


class PersonalitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    openness: int = Field(..., ge=0, le=100)
    conscientiousness: int = Field(..., ge=0, le=100)
    extraversion: int = Field(..., ge=0, le=100)
    agreeableness: int = Field(..., ge=0, le=100)
    neuroticism: int = Field(..., ge=0, le=100)


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    gender: Literal["m", "f", "x"] = "x"
    role_initial: str
    class_initial: str
    personality: PersonalitySpec
    values: dict[str, int] = Field(default_factory=dict)
    backstory: str = ""
    initial_opinions: dict[str, str] = Field(default_factory=dict)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation: SimulationConfig
    inference: InferenceConfig
    memory: MemoryConfig
    social: SocialConfig
    conversation: ConversationConfig
    voting: VotingConfig
    classes: list[str]
    roles: list[RoleSpec]
    agents: list[AgentSpec]

    @field_validator("classes")
    @classmethod
    def _classes_unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("classes must be unique")
        if not v:
            raise ValueError("at least one class required")
        return v

    @field_validator("roles")
    @classmethod
    def _roles_unique(cls, v: list[RoleSpec]) -> list[RoleSpec]:
        ids = [r.id for r in v]
        if len(set(ids)) != len(ids):
            raise ValueError("role ids must be unique")
        if not v:
            raise ValueError("at least one role required")
        return v

    @field_validator("agents")
    @classmethod
    def _agents_unique(cls, v: list[AgentSpec]) -> list[AgentSpec]:
        ids = [a.id for a in v]
        if len(set(ids)) != len(ids):
            raise ValueError("agent ids must be unique")
        if not v:
            raise ValueError("at least one agent required")
        return v

    @model_validator(mode="after")
    def _cross_check(self) -> "Config":
        role_ids = {r.id for r in self.roles}
        class_ids = set(self.classes)
        for a in self.agents:
            if a.role_initial not in role_ids:
                raise ValueError(
                    f"agent {a.id}: role_initial '{a.role_initial}' not in roles table"
                )
            if a.class_initial not in class_ids:
                raise ValueError(
                    f"agent {a.id}: class_initial '{a.class_initial}' not in classes list"
                )
        return self

def load_config(path: str | Path) -> Config:
    """Read a JSON config file from disk and validate it."""
    p = Path(path)
    raw: Any = json.loads(p.read_text(encoding="utf-8"))
    return Config.model_validate(raw)
