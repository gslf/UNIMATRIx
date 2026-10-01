"""A fixed learner must use supplied knowledge before a short task window opens."""

import json

import pytest

from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.recipes import RecipeRepository
from unimatrix.benchmark.scheduler import run_episode
from unimatrix.persistence.event_store import EventStore
from unimatrix.persistence.replay import replay
from unimatrix.scenarios.layers import layers_key


@pytest.mark.parametrize('teacher', ['reciprocal', 'oracle'])
@pytest.mark.parametrize('seed, condition', [(51000, 'standard'), (53000, 'stress')])
async def test_complete_retention_with_early_manual_and_fixed_learner(tmp_path, teacher, seed, condition):

    template = next(c for c in RecipeRepository().get('standard-v1')['cases']
                    if c['domain'] == 'D7' and layers_key(c['layers']) == condition)
    manifest = episode('D7', seed=seed, ticks=72, role='disadvantaged',
                       candidate=teacher, layers=template['layers'])
    result = await run_episode(manifest, tmp_path)
    assert result['metrics']['D7.retention']['normalized_value'] == 1
    assert result['diagnostics']['candidate_infrastructure_errors'] == 0
    store = EventStore(tmp_path / manifest['run_id'] / 'episode.db', read_only=True)
    try:
        assert replay(store)['transitions_recomputed'] == 72
        state = store.state_at(42)
        learner = state.scenario['learner']
        rows = store.db.execute('SELECT tick, observation, raw FROM decisions WHERE slot=?',
                                (learner,)).fetchall()
        prepared = []
        for tick, blob, raw in rows:
            observation = store.observation(blob)
            for op in json.loads(raw)['operations']:
                if op['verb'] != 'register_recipe' or observation['scenario']['tasks']:
                    continue
                assert observation['scenario']['procedures'] is None
                known = json.loads(observation['private_note'] or '{}').get('procedures', {})
                for obj in observation['objects'].values():
                    if obj.get('kind') == 'artifact' and learner in obj.get('read_by', []):
                        known.update(json.loads(obj['content']).get('procedures', {}))
                assert [step['transform_id'] for step in op['steps']] in known.values()
                prepared.append(tick)
        assert any(state.scenario['shock_tick'] <= tick < 43 for tick in prepared)
    finally:
        store.close()
