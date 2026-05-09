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
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p
