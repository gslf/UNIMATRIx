"""Administrative run identity must not change a model's observable world."""
from copy import deepcopy

import pytest

from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.recipes import RecipeRepository
from unimatrix.benchmark.scheduler import run_episode
from unimatrix.core.ids import digest
from unimatrix.persistence.event_store import EventStore
from unimatrix.persistence.replay import replay
from unimatrix.scenarios.layers import layers_key


def observations(store):
    return [
        (tick, slot, store.observation(blob))
        for tick, slot, blob in store.db.execute(
            "SELECT tick,slot,observation FROM decisions ORDER BY tick,slot"
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", [f"D{i}" for i in range(1, 9)])
async def test_administrative_suite_identity_preserves_all_actor_inputs(domain, tmp_path):
    layers = next(c["layers"] for c in RecipeRepository().get("standard-v1")["cases"]
                  if c["domain"] == domain and layers_key(c["layers"]) == "stress")
    left = episode(domain=domain, seed=67300, role="disadvantaged", layers=layers,
                   ticks=72, candidate="coordinator")
    right = deepcopy(left)
    right["suite_hash"] = "same-world-different-administrative-label"
    right.pop("run_id")
    right["run_id"] = digest(right)[:24]
    assert left["run_id"] != right["run_id"]


    await run_episode(left, tmp_path, until=6)
    await run_episode(right, tmp_path, until=6)
    a = EventStore(tmp_path / left["run_id"] / "episode.db", read_only=True)
    b = EventStore(tmp_path / right["run_id"] / "episode.db", read_only=True)
    try:
        assert observations(a) == observations(b)
        for tick in range(7):
            x, y = a.state_at(tick).dump(), b.state_at(tick).dump()
            x.pop("run_id")
            y.pop("run_id")
            assert x == y
        assert replay(a)["transitions_recomputed"] == 6
        assert replay(b)["transitions_recomputed"] == 6
    finally:
        a.close()
        b.close()


def test_visible_message_identity_changes_with_world_but_not_run_label():
    from unimatrix.actions.resolver import resolve
    from unimatrix.actions.schemas import empty
    from unimatrix.core.visibility import observe
    from unimatrix.scenarios import get_scenario

    def send(domain="D1", seed=701, replicate=0, label="one"):
        manifest = episode(domain=domain, seed=seed, replicate=replicate,
                           suite_hash=label, ticks=72, candidate="passive")
        scenario = get_scenario(domain)
        state = scenario.build(manifest)
        packets = {slot: observe(state, slot, scenario) for slot in state.agents}
        decisions = {slot: empty(0, slot) for slot in state.agents}
        decisions["slot-0"]["messages"] = [dict(channel="public", to=[], content="hello")]
        _, events = resolve(state, decisions, scenario, packets)
        return next(e["payload"]["id"] for e in events if e["type"] == "message_sent")

    first = send()
    assert send(label="different-administrative-label") == first
    assert len({first, send(seed=702), send(replicate=1), send(domain="D2")}) == 4
