"""Shared fixtures: a stub-backed simulation built from the shipped
standard.json, so tests exercise the real, current config schema and the real
orchestrator without needing a model server or embeddings."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STANDARD = REPO / "config" / "standard.json"


@pytest.fixture
def stub_config(tmp_path) -> Path:
    """The shipped standard config, switched to the stub backend with embeddings
    disabled, written to a temp path."""
    cfg = json.loads(STANDARD.read_text(encoding="utf-8"))
    cfg["inference"]["backend"] = "stub"
    cfg["memory"]["embedding_model"] = ""
    # Fast ticks and no checkpoint pauses, so real-time loop tests cover several
    # ticks within their short windows.
    cfg["simulation"]["tick_interval_seconds"] = 0.05
    cfg["simulation"]["auto_checkpoint_minutes"] = 999
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p
