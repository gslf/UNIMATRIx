"""Evidence attribution follows source roles across persistence and mapping order."""
import pytest

from unimatrix.actions.resolver import resolve
from unimatrix.actions.schemas import empty
from unimatrix.benchmark.manifests import episode
from unimatrix.core.state import replica
from unimatrix.core.visibility import observe
from unimatrix.persistence.event_store import EventStore
from unimatrix.scenarios import get_scenario


@pytest.mark.parametrize('preset', ['lenient', 'standard', 'harsh'])
def test_inspection_keeps_declared_source_and_signal_after_persistence(tmp_path, preset):
    manifest = episode('D1', layers=preset, seed=87, ticks=72)
    scenario = get_scenario('D1')
    initial = scenario.build(manifest)
    focal = manifest['focal_slot']
    window = initial.scenario['windows'][0]
    expected_source = (max(initial.scenario['accuracies'], key=initial.scenario['accuracies'].get)
                       if 'inspection' in window else min(window['signals']))
    expected_signal = window.get('inspection', window['signals'][expected_source])
    with_store = EventStore(tmp_path / 'episode.db')
    try:
        with_store.initialize(manifest, initial)
        restored = with_store.load()
    finally:
        with_store.close()
    reordered = restored.clone()
    reordered.scenario['windows'][0]['signals'] = dict(
        reversed(list(reordered.scenario['windows'][0]['signals'].items())))
    reordered.scenario['accuracies'] = dict(reversed(list(reordered.scenario['accuracies'].items())))
    for state in [initial, restored, reordered]:
        decision = dict(empty(0, focal), operations=[dict(verb='inspect', target_id='signal')])
        packet = observe(state, focal, scenario)
        after, events = resolve(state, {focal: decision}, scenario, {focal: packet})
        evidence = [e for e in events if e['type'] == 'evidence_observed']
        assert len(evidence) == 1
        assert evidence[0]['payload'] == dict(source=expected_source, signal=expected_signal)
        assert after.agents[focal]['inventory']['energy'] == state.agents[focal]['inventory']['energy'] - 1000


def test_initial_source_observation_matches_persisted_runner_input(tmp_path):
    manifest = episode('D1', seed=87, ticks=72)
    scenario = get_scenario('D1')
    initial = scenario.build(manifest)
    store = EventStore(tmp_path / 'episode.db')
    try:
        store.initialize(manifest, initial)
        restored = store.load()
        for slot in initial.agents:
            assert observe(initial, slot, scenario) == observe(restored, slot, scenario)
        reordered = replica(initial.scenario['windows'][0]['signals'])
        initial.scenario['windows'][0]['signals'] = dict(reversed(list(reordered.items())))
        assert observe(initial, manifest['focal_slot'], scenario) == observe(restored, manifest['focal_slot'], scenario)
    finally:
        store.close()
