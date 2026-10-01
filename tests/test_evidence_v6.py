"""Evidence format v6: hash-only commits, separate memory logs, compressed observations."""

import json
import os

import pytest

from tests.test_benchmark_core import setup
from unimatrix.actions.schemas import VALIDATOR, empty, interface, validate
from unimatrix.benchmark.runner import Runner
from unimatrix.core.ids import canonical, digest
from unimatrix.persistence.event_store import EventStore
from unimatrix.policies.router import Router


async def test_state_committed_carries_hashes_only(tmp_path):
    manifest, scenario, state, store = setup(tmp_path)
    final = await Runner(store, scenario, Router.scripted(manifest)).run(3)
    committed = list(store.events(kinds=["state_committed"]))
    assert len(committed) == 3
    assert set(committed[-1]["payload"]) == {"before_hash", "after_hash", "memories_hash"}
    assert committed[-1]["payload"]["after_hash"] == digest(final.dump())
    assert "memories" not in store.snapshot(3)
    blob = store.db.execute("SELECT observation FROM decisions LIMIT 1").fetchone()[0]
    assert isinstance(blob, bytes) and store.observation(blob)["tick"] == 0
    assert store.verify()["completed_tick"] == 3
    store.close()


async def test_memories_survive_resume_and_turnover(tmp_path):
    manifest, scenario, state, store = setup(tmp_path, "D7")
    shock = state.scenario["shock_tick"]
    live = await Runner(store, scenario, Router.scripted(manifest)).run(shock + 1)
    store.close()
    store = EventStore(tmp_path / "episode.db")
    loaded = store.load()
    assert loaded.memories == live.memories
    replaced = [slot for slot, a in live.agents.items() if a["generation"] == 1]
    assert replaced, "D7 replaces the learner at the turnover tick"
    old_rows = store.db.execute(
        "SELECT COUNT(*) FROM memories WHERE slot=? AND generation=0", (replaced[0],)
    ).fetchone()[0]
    assert old_rows > 0
    assert all(entry["tick"] >= shock for entry in loaded.memories[replaced[0]])
    await Runner(store, scenario, Router.scripted(manifest)).run(123)
    assert store.verify()["completed_tick"] == 123
    store.close()


def test_verb_first_validation_matches_oneof():
    shapes = interface()["operations"]

    def sample(shape):
        if isinstance(shape, dict):
            return {k: sample(v) for k, v in shape.items()}
        if isinstance(shape, list):
            return [sample(shape[0])]
        if shape == "integer":
            return 1
        if shape == "number":
            return 0.5
        if shape == "boolean":
            return True
        if "|" in shape:
            return shape.split("|")[0]
        return "x"

    candidates = []
    for verb, shape in shapes.items():
        op = dict(sample(shape), verb=verb)
        candidates.append(op)
        candidates.append(dict(op, extra=1))
        candidates.append({k: v for k, v in op.items() if k != next(iter(shape), "verb")})
        candidates.append(dict(op, verb="not-a-verb"))
    candidates.append({})
    for op in candidates:
        envelope = dict(empty(0, "a"), operations=[op])
        expected = VALIDATOR.is_valid(envelope)
        try:
            validate(canonical(envelope), 0, "a")
            actual = True
        except Exception:
            actual = False
        assert actual == expected, op


@pytest.mark.parametrize("value", [float("nan"), 1e400])
def test_non_finite_numbers_are_rejected(value):
    envelope = dict(empty(0, "a"), forecasts=[dict(probe_id="p", probabilities=[value, 0.5])])
    with pytest.raises(ValueError):
        validate(json.dumps(envelope), 0, "a")


def child_pid(manifest, directory, until):
    return dict(pid=os.getpid())
