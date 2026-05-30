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

_OFFICE_POWERS = ("legislative", "judicial", "financial")


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
    max_tokens_per_message: int = Field(200, ge=1)
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
    model_config = ConfigDict(extra="forbid")

    social_need_initial: float = 100.0
    social_need_decay_per_tick: float = Field(5.0, ge=0.0)
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


class VotingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    election_interval_ticks: int = Field(100, ge=1)
    max_ticks_without_vote: int = Field(10, ge=1)
    warmup_ticks: int = Field(4, ge=0)
    debate_rounds: int = Field(1, ge=0)
    max_tokens_per_debate_speech: int = Field(2000, ge=10)
    max_vote_attempts: int = Field(3, ge=1)
    election_ballot_max_tokens: int = Field(2000, ge=16)


class ClassThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    popularity_min: float = Field(0.0, ge=0)
    balance_min: float = Field(0.0, ge=0)


class MobilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_thresholds: dict[str, ClassThreshold] | None = None
    influence_step: float = Field(5.0, ge=0)
    max_influence_targets: int = Field(3, ge=1)
    prestige_decay_per_tick: float = Field(0.0, ge=0)
    popularity_decay_per_tick: float = Field(0.0, ge=0)


class OfficePowersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    senator_prestige_power: float = Field(20.0, ge=0)
    judge_popularity_power: float = Field(20.0, ge=0)
    judge_fine_fraction: float = Field(0.25, ge=0, le=1)
    banker_transfer_max: float = Field(200.0, ge=0)
    max_targets_per_power: int = Field(3, ge=1)


class AgentPowersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steal_success_prob: float = Field(0.5, ge=0, le=1)
    steal_max: float = Field(50.0, ge=0)
    steal_caught_popularity_penalty: float = Field(10.0, ge=0)
    # gift/bribe: cap on a single voluntary agent->agent transfer.
    gift_max: float = Field(100.0, ge=0)
    # sabotage: probability the saboteur blocks the target office's next power
    # use; the popularity penalty the saboteur pays if caught.
    sabotage_success_prob: float = Field(0.4, ge=0, le=1)
    sabotage_caught_popularity_penalty: float = Field(15.0, ge=0)


class EconomyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


    salary_per_prestige: float = Field(0.5, ge=0)
    tax_rate: float = Field(0.3, ge=0, le=1)
    agent_initial_balance: float = Field(100.0, ge=0)
    agent_expense_per_tick: float = Field(5.0, ge=0)
    community_initial_balance: float = Field(10000.0, ge=0)
    community_expense_per_tick: float = Field(50.0, ge=0)
    community_bankruptcy_balance: float = Field(0.0)
    destitution_exit_balance: float = Field(50.0, ge=0)
    protected_roles: list[str] = Field(
        default_factory=lambda: ["senator", "judge", "banker"]
    )
    loan_max_per_request: float = Field(200.0, ge=0)
    loan_interest_rate: float = Field(0.1, ge=0)
    loan_installments: int = Field(20, ge=1)


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
    opinions: dict[str, str] = Field(default_factory=dict)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation: SimulationConfig
    inference: InferenceConfig
    memory: MemoryConfig
    social: SocialConfig
    messaging: MessagingConfig = Field(default_factory=MessagingConfig)
    voting: VotingConfig
    economy: EconomyConfig = Field(default_factory=EconomyConfig)
    mobility: MobilityConfig = Field(default_factory=MobilityConfig)
    office_powers: OfficePowersConfig = Field(default_factory=OfficePowersConfig)
    agent_powers: AgentPowersConfig = Field(default_factory=AgentPowersConfig)
    offices: list[str] | None = None
    classes: list[str]
    roles: list[RoleSpec]
    agents: list[AgentSpec]

    # ----- office / power resolution -----

    def office_ids(self) -> list[str]:
        """The three elected office role-ids, in canonical (power) order."""
        return list(self.offices) if self.offices is not None else list(
            self.economy.protected_roles
        )

    def power_of_office(self, office_id: str) -> str | None:
        """Map an office role-id to its power kind, or None if not an office."""
        ids = self.office_ids()
        if office_id in ids:
            i = ids.index(office_id)
            if i < len(_OFFICE_POWERS):
                return _OFFICE_POWERS[i]
        return None

    def office_for_power(self, power: str) -> str | None:
        """Map a power kind ('legislative'|'judicial'|'financial') to its
        office role-id, or None."""
        ids = self.office_ids()
        try:
            i = _OFFICE_POWERS.index(power)
        except ValueError:
            return None
        return ids[i] if i < len(ids) else None

    def ordinary_role_ids(self) -> list[str]:
        """Role ids that are NOT elected offices (the prestige-driven pool)."""
        offices = set(self.office_ids())
        return [r.id for r in self.roles if r.id not in offices]

    def resolved_class_thresholds(self) -> dict[str, ClassThreshold]:
        """class_thresholds as given, or an auto-derived default ladder
        (balance_min=0 everywhere; popularity_min evenly spaced over the class
        order, lowest class = 0)."""
        if self.mobility.class_thresholds:
            return dict(self.mobility.class_thresholds)
        n = len(self.classes)
        out: dict[str, ClassThreshold] = {}

        for i, c in enumerate(self.classes):
            rank_from_bottom = (n - 1 - i)
            pop = (100.0 * rank_from_bottom / (n - 1)) if n > 1 else 0.0
            out[c] = ClassThreshold(popularity_min=pop, balance_min=0.0)
        return out

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
        role_counts: dict[str, int] = {}
        for a in self.agents:
            role_counts[a.role_initial] = role_counts.get(a.role_initial, 0) + 1
        for pr in self.economy.protected_roles:
            if pr not in role_ids:
                raise ValueError(
                    f"economy.protected_roles entry '{pr}' is not in the roles table"
                )
            if role_counts.get(pr, 0) < 1:
                raise ValueError(
                    f"initial population has no agent with protected role '{pr}'"
                )

        office_ids = self.office_ids()
        if len(office_ids) != 3:
            raise ValueError(
                "exactly 3 elected offices are required "
                f"(got {len(office_ids)}: {office_ids}); set economy.protected_roles "
                "or offices to three role ids"
            )
        for oid in office_ids:
            if oid not in role_ids:
                raise ValueError(f"office '{oid}' is not in the roles table")
            if role_counts.get(oid, 0) < 1:
                raise ValueError(
                    f"initial population has no agent with office role '{oid}'"
                )
        if self.mobility.class_thresholds is not None:
            tk = set(self.mobility.class_thresholds)
            if tk != class_ids:
                raise ValueError(
                    "mobility.class_thresholds keys must match classes exactly; "
                    f"got {sorted(tk)} vs classes {sorted(class_ids)}"
                )
        step = self.mobility.influence_step
        if step >= self.office_powers.senator_prestige_power:
            raise ValueError(
                "mobility.influence_step must be < office_powers."
                "senator_prestige_power (ordinary influence smaller than offices)"
            )
        if step >= self.office_powers.judge_popularity_power:
            raise ValueError(
                "mobility.influence_step must be < office_powers."
                "judge_popularity_power (ordinary influence smaller than offices)"
            )
        return self

def load_config(path: str | Path) -> Config:
    """Read a JSON config file from disk and validate it."""
    p = Path(path)
    raw: Any = json.loads(p.read_text(encoding="utf-8"))
    return Config.model_validate(raw)
