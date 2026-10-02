"""Whole-trajectory regression for the deadline lost to identical manuals."""
import json

import pytest

from tests.d7_pressure_teacher import CurriculumTeacher
from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.recipes import RecipeRepository
from unimatrix.benchmark.scheduler import run_episode
from unimatrix.persistence.event_store import EventStore
from unimatrix.persistence.replay import replay
from unimatrix.scenarios.layers import layers_key


@pytest.mark.parametrize('redundant', [False, True])
async def test_correct_teacher_retains_all_tasks_after_second_turnover(tmp_path, redundant):

    template = next(c for c in RecipeRepository().get('standard-v1')['cases']
                    if c['domain'] == 'D7' and layers_key(c['layers']) == 'stress')
    manifest = episode('D7', seed=66101, role='disadvantaged', ticks=72,
                       layers=template['layers'], candidate='reciprocal')

    def wrap(router):
        router.bindings[manifest['focal_slot']] = CurriculumTeacher(redundant)
        return router

    result = await run_episode(manifest, tmp_path, wrap=wrap)
    assert result['status'] == 'completed'
    assert all(value['raw_value'] == 1 for value in result['metrics'].values())
    store = EventStore(tmp_path/manifest['run_id']/'episode.db', read_only=True)
    try:
        assert replay(store)['transitions_recomputed'] == 72
        learner = store.state_at(0).scenario['learner']

        rows = store.db.execute('select raw from decisions where slot=?', (learner,)).fetchall()
        assert sum([op['verb'] for op in json.loads(raw)['operations']] == ['inspect']
                   for (raw,) in rows) == 3
    finally:
        store.close()
