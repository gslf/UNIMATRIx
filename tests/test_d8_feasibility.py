"""Constructive certificates use both legal operation slots during D8 repairs."""

import pytest

from unimatrix.actions.resolver import resolve
from unimatrix.benchmark.feasibility import witness
from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.recipes import RecipeRepository
from unimatrix.core.timing import span
from unimatrix.scenarios import get_scenario
from unimatrix.scenarios.layers import layers_key


@pytest.mark.parametrize('seed,role', [(1846588566, 'disadvantaged'), (1388805139, 'advantaged')])
def test_d8_witness_delivers_while_repairing_without_false_infeasibility(seed, role):
    base = RecipeRepository().get('standard-v1')
    case = next(c for c in base['cases'] if c['domain'] == 'D8' and layers_key(c['layers']) == 'stress')
    manifest = episode('D8', seed=seed, role=role, layers=case['layers'], ticks=72)
    scenario = get_scenario('D8')
    state = scenario.build(manifest)
    combined = []
    for _ in range(72):
        decisions = witness(state, scenario)
        for slot, decision in decisions.items():
            assert len(decision['operations']) <= 2
            if len(decision['operations']) == 2 and any(
                op == {'verb': 'work', 'project_id': 'service'} for op in decision['operations']
            ):
                confirmation = state.scenario['confirmed'][slot]
                assert confirmation['code'] == scenario.expected_code(state, slot)
                combined.append((state.tick, slot))
        state, events = resolve(state, decisions, scenario, {slot: {'inbox': []} for slot in decisions})
        assert not any(e['type'] == 'operation_rejected' for e in events)
        state.memories = {slot: [] for slot in state.agents}
        state.inbox = {slot: [] for slot in state.agents}
    assert all(result['success'] for result in state.scenario['results'])
    assert combined
    start = state.scenario['shock_tick'] + span(state, 20)
    assert sum(state.scenario['samples'][start:]) / (72 - start) >= .6
    certificate = scenario.feasible(manifest)
    assert certificate['valid'] and certificate['steps'] == 72
