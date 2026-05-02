from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from unimatrix.config import load_config


def test_loads_example(example_config_path: Path) -> None:
    cfg = load_config(example_config_path)
    assert cfg.simulation.name == "first_run"
    assert len(cfg.agents) == 50
    assert len(cfg.roles) == 15
    role_ids = {r.id for r in cfg.roles}
    for a in cfg.agents:
        assert a.role_initial in role_ids
        assert a.class_initial in cfg.classes


def test_unknown_role_fails(example_config_path: Path, tmp_path: Path) -> None:
    raw = json.loads(example_config_path.read_text(encoding="utf-8"))
    raw["agents"][0]["role_initial"] = "nonexistent"
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        load_config(p)


def test_duplicate_agent_id_fails(example_config_path: Path, tmp_path: Path) -> None:
    raw = json.loads(example_config_path.read_text(encoding="utf-8"))
    raw["agents"][1]["id"] = raw["agents"][0]["id"]
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        load_config(p)
