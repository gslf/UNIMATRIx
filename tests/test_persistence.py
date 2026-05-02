from __future__ import annotations

import json
from pathlib import Path

from unimatrix.persistence import Registry, RunStore


def test_run_store_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    store = RunStore(db)
    store.upsert_agent({
        "agent_id": "a1", "name": "Alice", "gender": "f",
        "role": "scholar", "class": "bourgeoisie",
        "personality": {"openness": 70},
        "values": {"loyalty": 50},
        "backstory": "...", "social_need": 100.0, "state": "idle",
        "current_conversation_id": None,
    })
    got = store.get_agent("a1")
    assert got["name"] == "Alice"
    assert got["state"] == "idle"

    cid = store.open_conversation("1to1", "a1", ["a1", "a2"])
    store.update_conversation_participants(cid, ["a1", "a2", "a3"])
    conv = store.get_conversation(cid)
    assert json.loads(conv["participants"]) == ["a1", "a2", "a3"]
    store.append_message(cid, 1, "a1", "hello")
    store.append_message(cid, 2, "a2", "hi back")
    msgs = store.get_messages(cid)
    assert [m["sender_id"] for m in msgs] == ["a1", "a2"]
    store.close_conversation(cid, "natural", "they greeted each other")
    conv = store.get_conversation(cid)
    assert conv["end_reason"] == "natural"

    pid = store.open_proposal("a1", "a2", "role", "worker", "scholar")
    store.record_vote(pid, "a1", "yes", "deserves it")
    store.record_vote(pid, "a2", "no", "not now")
    store.close_proposal(pid, "approved", 1, 1)  # tied → still 'approved' here
    votes = store.get_votes(pid)
    assert len(votes) == 2

    store.record_event("broadcast_human", {"message": "hello world"})
    events = store.recent_events(5)
    assert events[-1]["event_type"] == "broadcast_human"
    assert events[-1]["payload"]["message"] == "hello world"
    store.close()


def test_registry(tmp_path: Path) -> None:
    reg = Registry(tmp_path)
    rid = reg.register("first", str(tmp_path / "first.db"), {"k": 1})
    assert rid > 0
    runs = reg.list_runs()
    assert any(r["id"] == rid for r in runs)
    reg.update_status(rid, "ended", "2026-01-01T00:00:00Z")
    assert reg.get(rid)["status"] == "ended"
    reg.delete(rid)
    assert reg.get(rid) is None
