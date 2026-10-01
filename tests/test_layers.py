"""Layer presets, resolution and strict validation."""

import pytest

from unimatrix.benchmark.manifests import episode
from unimatrix.scenarios import get_scenario
from unimatrix.scenarios.layers import (
    LAYERS,
    PRESETS,
    catalog,
    layers_key,
    resolve_layers,
    validate_layers,
)


def test_presets_are_complete_and_named():
    for name, preset in PRESETS.items():
        assert validate_layers(preset) is preset
        assert layers_key(name) == name
        assert resolve_layers(name) == preset and resolve_layers(name) is not preset
    assert set(catalog()) == set(LAYERS)


def test_partial_objects_override_the_standard_preset():
    custom = resolve_layers({"scarcity": {"workshop_capacity": 4}})
    assert custom["scarcity"]["workshop_capacity"] == 4
    assert custom["pressure"] == PRESETS["standard"]["pressure"]
    assert layers_key(custom).startswith("custom-")
    assert layers_key({"preset": "harsh"}) == "harsh"
    assert layers_key({"preset": "lenient", "shock": {"task_fault": True}}).startswith("custom-")


@pytest.mark.parametrize(
    "value",
    [
        "extreme",
        {"preset": "nope"},
        {"unknown": {}},
        {"scarcity": {"workshop_capacity": 0}},
        {"scarcity": {"workshop_capacity": True}},
        {"shock": {"task_fault": 1}},
        {"shock": {"typo": True}},
        [],
    ],
)
def test_invalid_layers_are_rejected(value):
    with pytest.raises(ValueError):
        resolve_layers(value)


def test_validation_requires_every_parameter():
    partial = resolve_layers("standard")
    del partial["shock"]["task_fault"]
    with pytest.raises(ValueError, match="invalid_layer:shock"):
        validate_layers(partial)


def test_custom_layers_reach_the_scenario_and_the_manifest_identity():
    manifest = episode("D4", layers={"information": {"commons_transparency": True}})
    state = get_scenario("D4").build(manifest)
    assert state.scenario["layers"]["information"]["commons_transparency"] is True
    packet = get_scenario("D4").observation(state, manifest["focal_slot"])
    assert packet["stock"] == state.scenario["stock"]
    assert manifest["run_id"] != episode("D4")["run_id"]
