"""Provider-free re-execution and consistent SQLite backup of a recorded episode."""

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from jsonschema import ValidationError

from ..actions.resolver import resolve
from ..actions.schemas import validate
from ..benchmark.validation import validate_manifest
from ..core.ids import digest
from ..core.visibility import observe
from ..core.waiting import decision_packets
from ..scenarios import get_scenario
from .event_store import EventStore


def replay(store):
    original = store.verify()
    validate_manifest(store.manifest)
    scenario = get_scenario(store.manifest["domain"])
    state = scenario.build(store.manifest)
    if state.dump() != store.snapshot(0):
        raise ValueError("replay_initial_state_mismatch")
    with TemporaryDirectory(prefix="unimatrix-replay-") as temp:
        target = EventStore(Path(temp) / "episode.db")
        try:
            target.initialize(store.manifest, state)
            state = target.load()
            while state.tick < original["completed_tick"]:
                packets = {
                    s: observe(state, s, scenario)
                    for s, a in sorted(state.agents.items())
                    if a["alive"]
                }
                rows = {
                    s: (request, packet, raw)
                    for s, request, packet, raw in store.db.execute(
                        "SELECT slot,request_id,observation,raw FROM decisions WHERE tick=?",
                        (state.tick,),
                    )
                }
                active = decision_packets(state, packets)
                if set(rows) != set(active):
                    raise ValueError("replay_decision_set_mismatch")
                decisions = {}
                for slot, packet in active.items():
                    request, recorded, raw = rows[slot]
                    if store.observation(recorded) != packet:
                        raise ValueError(f"replay_observation_mismatch_at_{state.tick}")
                    target.save_decision(state.tick, slot, request, packet, raw)
                    try:
                        decisions[slot] = validate(raw, state.tick, slot)
                    except (ValueError, TypeError, ValidationError):
                        decisions[slot] = None
                after, events = resolve(state, decisions, scenario, packets)
                normalized = target.commit(state, after, events)
                if digest(after.dump()) != digest(store.snapshot(after.tick)):
                    raise ValueError(f"replay_transition_mismatch_at_{after.tick}")
                state = normalized
            if target.verify() != original:
                raise ValueError("replay_event_or_memory_mismatch")
        finally:
            target.close()
    return dict(original, transitions_recomputed=original["completed_tick"], providers_called=0)


def backup(source, destination):
    """Online SQLite backup includes WAL commits; never overwrite an existing target."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb"):
        pass
    try:
        db = sqlite3.connect(destination)
        try:
            source.db.backup(db)
        finally:
            db.close()
        restored = EventStore(destination, read_only=True)
        try:
            return restored.verify()
        finally:
            restored.close()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
