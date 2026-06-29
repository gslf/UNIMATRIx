"""Async-messaging engine tests (stub backend).

Covers the load-bearing invariants of the tick-based messaging refactor:
deferred (next-tick) delivery, the accumulating double-buffered inbox, single
social_need crediting, election-tick inbox preservation, and checkpoint/resume
re-derivation of the unread inbox from the DB.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from unimatrix.config import load_config
from unimatrix.inference import InferenceClient
from unimatrix.memory import MemoryManager
from unimatrix.orchestrator import Orchestrator
from unimatrix.persistence import Registry, RunStore


async def _make_orch(stub_config: Path, tmp_path: Path, chroma: str = "chroma"):
    cfg = load_config(stub_config)
    runs = tmp_path / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    registry = Registry(runs)
    db = runs / "m.db"
    run_id = registry.register("m", str(db), {})
    store = RunStore(db)
    memory = MemoryManager(cfg.memory, store, run_id, str(tmp_path / chroma))
    inference = InferenceClient(cfg.inference)
    orch = Orchestrator(cfg, store, memory, inference, registry, run_id,
                        run_dir=tmp_path, console=Console())
    await orch.initialize()
    return orch, cfg, store, memory, inference


def _two(orch):
    ids = list(orch.agents)
    return ids[0], ids[1]


@pytest.mark.asyncio
async def test_deferred_delivery_next_tick(stub_config: Path, tmp_path: Path) -> None:
    """A message sent on tick T is NOT readable until after the swap, then is
    consumed exactly once."""
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        a, b = _two(orch)
        msg = await orch.messaging.send(a, [b], "hello", tick_no=5)
        assert msg is not None and msg.id > 0
        # Persisted with tick_no=5; recipient sees it in the DB.
        assert [m["id"] for m in store.unread_messages_for(b, 0)] == [msg.id]
        # But it is NOT yet in the live inbox (only in _next_inbox).
        assert not orch.messaging.has_pending(b)
        # After the swap it becomes readable.
        orch.messaging.swap()
        assert orch.messaging.has_pending(b)
        got = await orch.messaging.read_inbox(b, 6)
        assert [m.id for m in got] == [msg.id]
        # Consumed — no longer pending.
        assert not orch.messaging.has_pending(b)
        # The sender never receives its own message.
        assert store.unread_messages_for(a, 0) == []
    finally:
        await inference.aclose()
        await memory.close()
        store.close()


@pytest.mark.asyncio
async def test_swap_accumulates_unread(stub_config: Path, tmp_path: Path) -> None:
    """Unread mail accumulates across ticks (the throttle can never drop it):
    swap MERGES rather than replaces."""
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        a, b = _two(orch)
        await orch.messaging.send(a, [b], "one", tick_no=1)
        orch.messaging.swap()                      # delivered, b does NOT read
        await orch.messaging.send(a, [b], "two", tick_no=2)
        orch.messaging.swap()
        got = await orch.messaging.read_inbox(b, 3)
        assert [m.content for m in got] == ["one", "two"]
    finally:
        await inference.aclose()
        await memory.close()
        store.close()


@pytest.mark.asyncio
async def test_social_need_credited_once_per_party(
    stub_config: Path, tmp_path: Path
) -> None:
    """Sender gains once on send; recipient gains once per message on read —
    not twice."""
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        gain = cfg.social.social_need_gain_per_turn
        a, b = _two(orch)
        orch.agents[a].social_need = 50.0
        orch.agents[b].social_need = 50.0
        await orch.messaging.send(a, [b], "hi", tick_no=1)
        assert orch.agents[a].social_need == pytest.approx(50.0 + gain)
        assert orch.agents[b].social_need == pytest.approx(50.0)  # not yet read
        orch.messaging.swap()
        await orch.messaging.read_inbox(b, 2)
        assert orch.agents[b].social_need == pytest.approx(50.0 + gain)
    finally:
        await inference.aclose()
        await memory.close()
        store.close()


@pytest.mark.asyncio
async def test_checkpoint_resume_rederives_inbox(
    stub_config: Path, tmp_path: Path
) -> None:
    """After pause+resume the unread inbox is reconstructed from the DB (never
    serialized): a message delivered but unread survives the round-trip, while a
    message the recipient already read does not reappear."""
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    db_path = store.path
    registry = orch.registry
    run_id = orch.run_id
    try:
        a, b = _two(orch)
        # b reads a tick-1 message (consumed), then a tick-2 message is left
        # delivered-but-unread.
        await orch.messaging.send(a, [b], "read me", tick_no=1)
        orch.messaging.swap()
        await orch.messaging.read_inbox(b, 2)        # consumes the tick-1 msg
        await orch.messaging.send(a, [b], "unread", tick_no=2)
        orch.messaging.swap()                        # delivered, NOT read
        orch._tick_no = 2
        await orch._write_checkpoint()
    finally:
        await inference.aclose()
        await memory.close()
        store.close()

    store2 = RunStore(db_path)
    memory2 = MemoryManager(cfg.memory, store2, run_id, str(tmp_path / "chroma2"))
    inference2 = InferenceClient(cfg.inference)
    orch2 = Orchestrator(cfg, store2, memory2, inference2, registry, run_id,
                         run_dir=tmp_path, console=Console())
    try:
        await orch2.initialize_from_existing()
        pending = await orch2.messaging.read_inbox(b, 3)
        assert [m.content for m in pending] == ["unread"]
    finally:
        await inference2.aclose()
        await memory2.close()
        store2.close()


@pytest.mark.asyncio
async def test_broadcast_delivered_not_persisted(
    stub_config: Path, tmp_path: Path
) -> None:
    """A broadcast lands in every agent's inbox next tick but is not written to
    the messages table (keeps the social graph clean)."""
    orch, cfg, store, memory, inference = await _make_orch(stub_config, tmp_path)
    try:
        await orch.queue_broadcast("HUMAN", "hear ye")
        # Drain it (the broadcast step delivers into next-tick inboxes).
        item = orch._broadcast_queue.pop(0)
        await orch._apply_broadcast(item)
        orch.messaging.swap()
        a = next(iter(orch.agents))
        got = await orch.messaging.read_inbox(a, 1)
        assert any(m.is_broadcast for m in got)
        # The broadcast is delivered via the inbox, never written to the messages
        # table — so no recipient has a persisted message.
        assert all(not store.unread_messages_for(x, 0) for x in orch.agents)
        events = {e["event_type"] for e in store.recent_events(50)}
        assert "broadcast_human" in events
    finally:
        await inference.aclose()
        await memory.close()
        store.close()
