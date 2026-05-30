from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from unimatrix.config import load_config, strip_removed_legacy_keys


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


def test_economy_defaults_applied_when_missing(
    example_config_path: Path, tmp_path: Path
) -> None:
    """A config without an `economy` block loads with EconomyConfig defaults,
    because the default protected roles (senator/judge/banker) exist in the role
    table and the initial population includes a holder of each."""
    raw = json.loads(example_config_path.read_text(encoding="utf-8"))
    raw.pop("economy", None)
    p = tmp_path / "no_economy.json"
    p.write_text(json.dumps(raw))
    cfg = load_config(p)
    assert cfg.economy.protected_roles == ["senator", "judge", "banker"]
    assert cfg.economy.tax_rate == 0.3
    assert cfg.economy.loan_installments == 20


def test_protected_role_must_exist(
    example_config_path: Path, tmp_path: Path
) -> None:
    raw = json.loads(example_config_path.read_text(encoding="utf-8"))
    raw["economy"]["protected_roles"] = ["nonexistent_role"]
    p = tmp_path / "bad_protected.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        load_config(p)


def test_protected_role_must_have_holder(
    example_config_path: Path, tmp_path: Path
) -> None:
    """If no agent starts in a protected role, the config is invalid."""
    raw = json.loads(example_config_path.read_text(encoding="utf-8"))
    # Reassign every banker to another role so nobody holds it on tick 1.
    for a in raw["agents"]:
        if a["role_initial"] == "banker":
            a["role_initial"] = "merchant"
    # Make sure "banker" is still in the protected list (it is by default).
    p = tmp_path / "no_banker.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        load_config(p)


def test_economy_block_round_trips(
    example_config_path: Path, tmp_path: Path
) -> None:
    cfg = load_config(example_config_path)
    assert cfg.economy.tax_rate >= 0
    assert cfg.economy.tax_rate <= 1
    assert cfg.economy.agent_initial_balance > 0
    # Every protected role must exist in the roles table — sanity check
    # the example config aligns with the rule the validator enforces.
    role_ids = {r.id for r in cfg.roles}
    for pr in cfg.economy.protected_roles:
        assert pr in role_ids


def test_mobility_and_office_defaults(example_config_path: Path) -> None:
    """A config without mobility/office_powers/agent_powers loads with defaults
    and resolves offices positionally to powers."""
    cfg = load_config(example_config_path)
    assert cfg.mobility.influence_step > 0
    assert cfg.office_ids() == cfg.economy.protected_roles
    assert cfg.power_of_office(cfg.office_ids()[0]) == "legislative"
    assert cfg.power_of_office(cfg.office_ids()[2]) == "financial"
    # Ordinary roles exclude the three offices.
    assert not (set(cfg.office_ids()) & set(cfg.ordinary_role_ids()))


def test_explicit_mobility_block_loads(
    example_config_path: Path, tmp_path: Path
) -> None:
    raw = json.loads(example_config_path.read_text(encoding="utf-8"))
    raw["mobility"] = {
        "influence_step": 4,
        "class_thresholds": {
            "aristocracy": {"popularity_min": 70, "balance_min": 100},
            "bourgeoisie": {"popularity_min": 40, "balance_min": 50},
            "people": {"popularity_min": 15, "balance_min": 0},
            "marginal": {"popularity_min": 0, "balance_min": 0},
        },
    }
    p = tmp_path / "with_mobility.json"
    p.write_text(json.dumps(raw))
    cfg = load_config(p)
    assert cfg.mobility.influence_step == 4
    assert cfg.resolved_class_thresholds()["aristocracy"].popularity_min == 70


def test_class_thresholds_must_match_classes(
    example_config_path: Path, tmp_path: Path
) -> None:
    raw = json.loads(example_config_path.read_text(encoding="utf-8"))
    raw["mobility"] = {
        "class_thresholds": {
            "aristocracy": {"popularity_min": 70, "balance_min": 100},
            # missing the other classes / wrong key
            "nonexistent": {"popularity_min": 0, "balance_min": 0},
        }
    }
    p = tmp_path / "bad_thresholds.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        load_config(p)


def test_influence_must_be_smaller_than_office(
    example_config_path: Path, tmp_path: Path
) -> None:
    raw = json.loads(example_config_path.read_text(encoding="utf-8"))
    raw["mobility"] = {"influence_step": 50}
    raw["office_powers"] = {"senator_prestige_power": 20, "judge_popularity_power": 20}
    p = tmp_path / "too_big_influence.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        load_config(p)


def test_strip_removed_legacy_keys_drops_retired_fields() -> None:
    raw = {
        "simulation": {"name": "x", "language": "en"},
        "voting": {"max_ticks_without_vote": 10, "warmup_ticks": 3},
    }
    out = strip_removed_legacy_keys(raw)
    assert "language" not in out["simulation"]
    assert "max_ticks_without_vote" not in out["voting"]
    # Untouched keys survive.
    assert out["voting"]["warmup_ticks"] == 3


def test_legacy_config_with_removed_fields_still_loads(
    example_config_path: Path, tmp_path: Path
) -> None:
    """A config saved before `language` / `max_ticks_without_vote` were retired
    (as the run registry stores them) must still validate under extra='forbid'."""
    raw = json.loads(example_config_path.read_text(encoding="utf-8"))
    raw["simulation"]["language"] = "en"
    raw["voting"]["max_ticks_without_vote"] = 10
    p = tmp_path / "legacy.json"
    p.write_text(json.dumps(raw))
    cfg = load_config(p)  # must not raise
    assert cfg.simulation.name == "first_run"
    assert not hasattr(cfg.simulation, "language")
