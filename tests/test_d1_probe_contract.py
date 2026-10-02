"""Every scored forecast is defined by public, answer-independent metadata."""

import pytest

from unimatrix.benchmark.manifests import episode
from unimatrix.core.timing import scaled
from unimatrix.core.visibility import observe
from unimatrix.scenarios import get_scenario


@pytest.mark.parametrize("preset", ["lenient", "standard", "harsh"])
@pytest.mark.parametrize("route,age", [(None, None), (0, 0), (1, 0), (2, 0), (1, 1)])
def test_public_choice_definition_matches_actual_resolution(preset, route, age):
    manifest = episode("D1", layers=preset, ticks=72, seed=87)
    scenario = get_scenario("D1")
    state = scenario.build(manifest)
    state.tick = scaled(72, 20) - 2
    focal = manifest["focal_slot"]
    window = state.scenario["windows"][0]
    packet = observe(state, focal, scenario)
    fact, choice = packet["scenario"]["probes"]
    assert fact["outcomes"] == [0, 1]
    assert fact["target"] == {"kind": "hidden_fact", "window": 0}
    target = choice["target"]
    assert target["kind"] == "agent_route"
    assert target["agent_id"] in packet["scenario"]["sources"]
    assert target["tick"] == packet["tick"]
    assert choice["outcomes"] == ["route-0", "route-1", "route-2 or no route in scope"]
    assert "probabilities[i]" in packet["protocol"]

    actor = target["agent_id"]
    if route is not None:
        window["choices"][actor] = route
        window["choice_ticks"][actor] = state.tick - age
    other = next(s for s in window["signals"] if s != actor)
    window["choices"][other] = 1 if route != 1 else 0
    window["choice_ticks"][other] = state.tick

    assert target["scope"] in {"current_tick", "current_window"}
    expected = route if route is not None else 2
    if target["scope"] == "current_tick" and age != 0:
        expected = 2
    scenario.evolve(state, state.clone())
    assert window["choice_target"] == expected

    window["target"] = 1 - window["target"]
    window["signals"] = {s: 1 - v for s, v in reversed(list(window["signals"].items()))}
    assert observe(state, focal, scenario) == packet


@pytest.mark.parametrize("ticks", [72, 240])
def test_probe_schedule_and_class_definitions_cover_all_scored_windows(ticks):
    manifest = episode("D1", ticks=ticks, seed=3)
    scenario = get_scenario("D1")
    state = scenario.build(manifest)
    focal = manifest["focal_slot"]
    count = 0
    for tick in range(ticks):
        state.tick = tick
        state.scenario["window"] = tick // scaled(ticks, 20)
        packet = observe(state, focal, scenario)
        probes = packet["scenario"]["probes"]
        if tick % scaled(ticks, 20) != packet["scenario"]["forecast_offset"]:
            assert probes == []
        for probe in probes:
            assert len(probe["outcomes"]) == probe["classes"]
            assert probe["target"]["kind"] in {"hidden_fact", "agent_route"}
            count += 1
        peer = next(s for s in state.agents if s != focal)
        assert observe(state, peer, scenario)["scenario"]["probes"] == []
    assert count == 18
