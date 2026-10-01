"""Shorter episodes retain scored opportunities, timing and evidence identity."""
from copy import deepcopy

import pytest

from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.recipes import RecipeRepository, bind_candidate, validate_recipe
from unimatrix.benchmark.scheduler import run_episode
from unimatrix.benchmark.validation import validate_manifest
from unimatrix.config.models import Config
from unimatrix.persistence.event_store import EventStore
from unimatrix.persistence.replay import replay
from unimatrix.research.probes import probe_ticks
from unimatrix.scenarios import get_scenario


def test_horizon_is_bound_in_manifest_and_configuration_identity():
    short, extended = episode('D1', ticks=72), episode('D1', ticks=240)
    assert short['run_id'] != extended['run_id']
    for manifest in (short, extended):
        validate_manifest(manifest)
        assert len(probe_ticks(manifest)) == 12
        assert max(probe_ticks(manifest)) == manifest['ticks'] - 2
    assert Config(mode='core', domain='D1', ticks=72).manifest()['ticks'] == 72
    with pytest.raises(ValueError, match='episode_shape'):
        validate_manifest(episode('D1', ticks=60))


def test_events_beyond_the_selected_horizon_are_rejected():
    spec = deepcopy(RecipeRepository().get('standard-v1'))
    case = next(c for c in spec['cases'] if isinstance(c['layers'], dict) and c['layers']['shock']['events'])
    case['layers']['shock']['events'][0]['when'] = {'at': spec['ticks']}
    with pytest.raises(ValueError, match='Invalid benchmark case'):
        validate_recipe(spec)


@pytest.mark.asyncio
@pytest.mark.parametrize('domain', [f'D{i}' for i in range(1, 9)])
async def test_complete_short_episode_replays_and_counts_all_scored_opportunities(tmp_path, domain):
    manifests = bind_candidate(RecipeRepository().get('standard-v1'), 'oracle')['episodes']
    manifest = next(m for m in manifests if m['domain'] == domain)
    assert manifest['ticks'] == 72
    certificate = get_scenario(domain).feasible(manifest)
    assert certificate['steps'] == 72
    result = await run_episode(manifest, tmp_path)
    assert result['status'] == 'completed'
    store = EventStore(tmp_path / manifest['run_id'] / 'episode.db', read_only=True)
    try:
        assert replay(store)['transitions_recomputed'] == 72
        expected = {
            'D1': ('probe_resolved', 12), 'D2': ('market_closed', 6),
            'D3': ('service_verified', 4), 'D4': ('service_sampled', 72),
            'D5': ('interaction_outcome', 12), 'D6': ('service_verified', 6),
            'D7': ('heldout_task_resolved', 15), 'D8': ('essential_obligation_resolved', 5),
        }
        kind, count = expected[domain]
        assert len(list(store.events(kinds=[kind]))) == count
        for metric in result['metrics'].values():
            assert metric['evidence_event_ids']
            assert 0 <= metric['normalized_value'] <= 1
    finally:
        store.close()
