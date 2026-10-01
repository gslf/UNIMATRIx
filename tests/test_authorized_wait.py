"""Waiting skips only explicitly delegated inactivity, with no fabricated responses."""

import asyncio

import pytest

from unimatrix.actions.resolver import resolve
from unimatrix.actions.schemas import empty, validate
from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.runner import Runner
from unimatrix.core.ids import canonical
from unimatrix.core.visibility import observe
from unimatrix.core.waiting import decision_packets, is_waiting
from unimatrix.evaluation.diagnostics import diagnostics
from unimatrix.persistence.event_store import EventStore
from unimatrix.persistence.replay import replay
from unimatrix.policies.router import Router
from unimatrix.policies.scripted import Scripted
from unimatrix.scenarios import get_scenario
from unimatrix.scenarios.base import replace_slot


class Waiter:
    fingerprint = 'test-explicit-wait-v1'

    def __init__(self, until=6):
        self.until, self.observations = until, []

    async def decide(self, o, budget):
        self.observations.append(o)
        d = empty(o['tick'], o['agent_id'])
        if o['tick'] == 0:
            d['operations'] = [dict(verb='wait', until_tick=self.until)]
            d['private_note'] = 'Wait was my decision.'
        return canonical(d), dict(generated_tokens=0, purpose='decision')


def world(tmp_path, waiter=None):
    manifest = episode('D6', seed=67000, ticks=72, candidate='reciprocal', layers='standard')
    scenario = get_scenario('D6')
    state = scenario.build(manifest)
    store = EventStore(tmp_path/'episode.db')
    store.initialize(manifest, state)
    waiter = waiter or Waiter()
    router = Router({s: Scripted('passive') for s in state.agents})
    router.bindings[manifest['focal_slot']] = waiter
    return manifest, scenario, state, store, router, waiter


async def test_explicit_wait_omits_calls_rows_and_preserves_world_clock_and_replay(tmp_path):
    m, scenario, _, store, router, waiter = world(tmp_path)
    try:
        state = await Runner(store, scenario, router).run(8)
        assert state.tick == 8
        assert [o['tick'] for o in waiter.observations] == [0, 6, 7]
        assert waiter.observations[1]['private_note'] == 'Wait was my decision.'
        assert all('_wait' not in o['self'] for o in waiter.observations)
        rows = store.db.execute('select tick from decisions where slot=? order by tick', (m['focal_slot'],)).fetchall()
        assert rows == [(0,), (6,), (7,)]
        waited = [e for e in store.events(kinds=['decision_waited']) if e['actor_id'] == m['focal_slot']]
        assert len(waited) == 5 and all(e['payload']['origin_tick'] == 0 for e in waited)
        d = diagnostics(store)
        assert d['candidate_resolved_decisions'] == d['candidate_decision_attempts'] == 3
        assert d['candidate_authorized_wait_ticks'] == 5
        assert d['candidate_invalid_envelopes'] == d['candidate_infrastructure_errors'] == 0
        assert replay(store)['transitions_recomputed'] == 8
    finally:
        store.close()


async def test_new_message_wakes_before_deadline_and_is_not_consumed_unseen(tmp_path):
    m, scenario, _, store, router, waiter = world(tmp_path)
    focal = m['focal_slot']

    class Sender:
        fingerprint = 'test-sender-v1'

        async def decide(self, o, budget):
            d = empty(o['tick'], o['agent_id'])
            if o['tick'] == 2:
                d['messages'] = [dict(channel='private', to=[focal], content='New opportunity')]
            return canonical(d), dict(generated_tokens=0, purpose='decision')

    router.bindings[next(s for s in router.bindings if s != focal)] = Sender()
    try:
        await Runner(store, scenario, router).run(5)
        assert [o['tick'] for o in waiter.observations] == [0, 3, 4]
        assert waiter.observations[1]['inbox'][0]['content'] == 'New opportunity'
        assert not waiter.observations[2]['inbox']
        assert store.state_at(3).inbox[focal]
        assert replay(store)['transitions_recomputed'] == 5
    finally:
        store.close()


async def test_resume_preserves_authorization_without_repeating_the_request(tmp_path):
    m, scenario, _, store, router, waiter = world(tmp_path)
    await Runner(store, scenario, router).run(3)
    store.close()
    resumed = EventStore(tmp_path/'episode.db')
    try:
        await Runner(resumed, scenario, router).run(8)
        assert [o['tick'] for o in waiter.observations] == [0, 6, 7]
        assert diagnostics(resumed)['candidate_authorized_wait_ticks'] == 5
        assert replay(resumed)['transitions_recomputed'] == 8
    finally:
        resumed.close()


def authorized_state(tmp_path):
    m, scenario, state, store, _, _ = world(tmp_path)
    store.close()
    focal = m['focal_slot']
    packet = observe(state, focal, scenario)
    d = empty(0, focal)
    d['operations'] = [dict(verb='wait', until_tick=60)]
    after, _ = resolve(state, {focal: d}, scenario, {focal: packet})
    assert is_waiting(after, focal, observe(after, focal, scenario))
    return scenario, after, focal


@pytest.mark.parametrize('change', ['inventory', 'mandate', 'profile', 'object', 'scenario', 'event', 'peer', 'omitted'])
def test_any_visible_information_change_wakes(tmp_path, change):
    scenario, state, focal = authorized_state(tmp_path)
    p = observe(state, focal, scenario)
    if change in {'inventory', 'mandate', 'profile'}:
        p['self'][change] = {'changed': True}
    elif change == 'object':
        p['objects']['new'] = {'kind': 'artifact'}
    elif change == 'scenario':
        p['scenario']['new_task'] = True
    elif change == 'event':
        p['events'].append(dict(type='new_observed_event'))
    elif change == 'peer':
        p['peers'][0]['alive'] = False
    else:
        p['omitted']['objects'] += 1
    assert not is_waiting(state, focal, p)


def test_clock_receipt_and_own_note_changes_do_not_wake_but_omitted_inbox_does(tmp_path):
    scenario, state, focal = authorized_state(tmp_path)
    p = observe(state, focal, scenario)
    p.update(tick=2, private_note='Own saved note', receipts=[])
    p['omitted']['note_bytes'] = 42
    assert is_waiting(state, focal, p)
    p['omitted']['inbox'] = 1
    assert not is_waiting(state, focal, p)


def test_hidden_changes_do_not_wake_and_turnover_never_inherits_wait(tmp_path):
    scenario, state, focal = authorized_state(tmp_path)
    other = next(s for s in state.agents if s != focal)
    state.agents[other]['note'] = 'Hidden private information'
    state.objects['secret'] = dict(kind='artifact', owner=other, visibility=[other], content='Secret')
    assert is_waiting(state, focal, observe(state, focal, scenario))
    replace_slot(state, focal)
    assert '_wait' not in state.agents[focal]
    assert not is_waiting(state, focal, observe(state, focal, scenario))


@pytest.mark.parametrize('deadline', [0, -1, True, 3.0, 3.5])
def test_wait_deadline_is_strict_positive_integer(deadline):
    d = empty(0, 'actor')
    d['operations'] = [dict(verb='wait', until_tick=deadline)]
    with pytest.raises(Exception):
        validate(canonical(d), 0, 'actor')


@pytest.mark.parametrize('conflict', ['operation', 'message', 'forecast', 'query', 'horizon'])
def test_invalid_wait_has_no_authorization(tmp_path, conflict):
    m, scenario, state, store, _, _ = world(tmp_path)
    store.close()
    focal = m['focal_slot']
    d = empty(0, focal)
    d['operations'] = [dict(verb='wait', until_tick=60)]
    if conflict == 'operation':
        d['operations'].append(dict(verb='revise_profile', profile='hello'))
    elif conflict == 'message':
        d['messages'] = [dict(channel='public', to=[], content='hello')]
    elif conflict == 'forecast':
        d['forecasts'] = [dict(probe_id='unknown', probabilities=[.5,.5])]
    elif conflict == 'query':
        d['memory_query'] = 'something'
    else:
        d['operations'][0]['until_tick'] = 73
    after, _ = resolve(state, {focal:d}, scenario, {focal:observe(state, focal, scenario)})
    assert '_wait' not in after.agents[focal]
    assert after.receipts[focal][0]['status'] == 'rejected'


async def test_replay_rejects_fabricated_response_during_wait(tmp_path):
    m, scenario, _, store, router, _ = world(tmp_path)
    try:
        await Runner(store, scenario, router).run(4)
        slot = m['focal_slot']
        state = store.state_at(1)
        p = observe(state, slot, scenario)
        assert slot not in decision_packets(state, {slot:p})
        store.save_decision(1, slot, 'fabricated', p, canonical(empty(1, slot)))
        with pytest.raises(ValueError, match='replay_decision_set_mismatch'):
            replay(store)
    finally:
        store.close()


async def test_pause_and_progress_do_not_count_authorized_wait_as_pending_request(tmp_path):
    from unimatrix.web.evaluation_explorer import progress

    m, scenario, _, store, router, waiter = world(tmp_path)
    entered = asyncio.Event()
    gate = asyncio.Event()
    peer = next(s for s in router.bindings if s != m['focal_slot'])

    class BlockingPeer:
        fingerprint = 'test-blocked-peer-v1'

        async def decide(self, o, budget):
            if o['tick'] == 3 and not gate.is_set():
                entered.set()
                await gate.wait()
            return canonical(empty(o['tick'], o['agent_id'])), dict(generated_tokens=0, purpose='decision')

    router.bindings[peer] = BlockingPeer()
    running = asyncio.create_task(Runner(store, scenario, router).run(8))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        current = progress(tmp_path/'episode.db', m, True)
        assert m['focal_slot'] in current['authorized_waiting']
        assert m['focal_slot'] not in current['waiting']
        assert peer in current['waiting']
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert store.status() == dict(status='paused', completed_tick=3)
        assert [o['tick'] for o in waiter.observations] == [0]
        gate.set()
        await Runner(store, scenario, router).run(8)
        assert [o['tick'] for o in waiter.observations] == [0, 6, 7]
        assert replay(store)['transitions_recomputed'] == 8
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        store.close()


async def test_wait_evidence_identifies_authorization_without_synthetic_response(tmp_path):
    from unimatrix.web.evaluation_explorer import decision_evidence

    m, scenario, _, store, router, _ = world(tmp_path)
    try:
        await Runner(store, scenario, router).run(4)
        data = decision_evidence(store, m['focal_slot'], 2)
        assert data == dict(recorded=False, observation=None, response=None, calls=[],
                            authorized_wait=dict(origin_tick=0, until_tick=6, generation=0))
        assert decision_evidence(store, m['focal_slot'], 0)['recorded']
        future = decision_evidence(store, m['focal_slot'], 5)
        assert 'authorized_wait' not in future
    finally:
        store.close()
