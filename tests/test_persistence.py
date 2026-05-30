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
    })
    got = store.get_agent("a1")
    assert got["name"] == "Alice"
    assert got["state"] == "idle"

    # Async-messaging roundtrip: one message to two recipients on tick 1.
    mid = store.add_message("a1", ["a2", "a3"], "hello", tick_no=1)
    assert mid > 0
    in_tick = store.messages_in_tick(1)
    assert len(in_tick) == 1
    assert in_tick[0]["sender_id"] == "a1"
    assert sorted(in_tick[0]["recipients"]) == ["a2", "a3"]
    # a2's inbox sees it; a1 (the sender) does not.
    assert [m["id"] for m in store.unread_messages_for("a2", 0)] == [mid]
    assert store.unread_messages_for("a1", 0) == []
    # Recipient-filtered feed and the sender view both find it.
    assert [m["id"] for m in store.list_messages(recipient="a3")] == [mid]
    assert [m["id"] for m in store.messages_by_sender("a1")] == [mid]
    # Pairwise thread a1↔a2.
    thread = store.messages_between("a1", "a2")
    assert [m["content"] for m in thread] == ["hello"]

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
