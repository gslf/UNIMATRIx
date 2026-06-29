from __future__ import annotations

import json
from pathlib import Path

from unimatrix.persistence import Registry, RunStore


def test_run_store_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    store = RunStore(db)
    store.upsert_agent({
        "agent_id": "a1", "name": "Alice", "gender": "f",
        "circumstance": "woke already in motion", "disposition": "restless",
        "self_model_json": {"identity_narrative": "I keep going."},
        "self_model_version": 1, "social_need": 100.0, "state": "idle",
    })
    got = store.get_agent("a1")
    assert got["name"] == "Alice"
    assert got["state"] == "idle"
    assert got["self_model_version"] == 1

    # Async-messaging roundtrip: one message to two recipients on tick 1.
    mid = store.add_message("a1", ["a2", "a3"], "hello", tick_no=1)
    assert mid > 0
    # a2's inbox sees it (with both recipients); a1 (the sender) does not.
    a2_unread = store.unread_messages_for("a2", 0)
    assert [m["id"] for m in a2_unread] == [mid]
    assert sorted(a2_unread[0]["recipients"]) == ["a2", "a3"]
    assert store.unread_messages_for("a1", 0) == []
    # Pairwise thread a1↔a2.
    thread = store.messages_between("a1", "a2")
    assert [m["content"] for m in thread] == ["hello"]

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
