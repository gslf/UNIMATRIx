"""Shared fixtures: a small in-memory simulation built with the stub backend."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def example_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "example_run.json"


@pytest.fixture
def stub_config(example_config_path, tmp_path) -> Path:
    """Tiny config: 6 agents, stub backend, fast tick."""
    cfg = json.loads(example_config_path.read_text(encoding="utf-8"))
    cfg["simulation"]["name"] = "test_run"
    cfg["simulation"]["tick_interval_seconds"] = 0.05
    cfg["simulation"]["auto_checkpoint_minutes"] = 999
    cfg["inference"]["backend"] = "stub"
    cfg["memory"]["embedding_model"] = ""
    cfg["social"]["silence_detection_seconds"] = 0.5
    cfg["agents"] = cfg["agents"][:6]
    # Roomy economy for tests: the smoke loop runs many ticks with a fast
    # interval, and the financial pass would otherwise drain the treasury
    # before the assertions land. Tests that specifically exercise the
    # economy override these in the test body.
    cfg["economy"] = {
        "salary_per_prestige": 0.5,
        "production_per_prestige": 0.0,
        "tax_rate": 0.3,
        "agent_initial_balance": 10000.0,
        "agent_expense_per_tick": 0.0,
        "community_initial_balance": 1_000_000.0,
        "community_expense_per_tick": 0.0,
        "community_bankruptcy_balance": 0.0,
        "destitution_exit_balance": 50.0,
        "protected_roles": ["president", "supreme_judge", "banker"],
        "loan_max_per_request": 200.0,
        "loan_interest_rate": 0.1,
        "loan_installments": 20,
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p
