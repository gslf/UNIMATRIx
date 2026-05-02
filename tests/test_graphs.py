"""Render every graph against an empty/tiny db; ensure no exceptions."""
from __future__ import annotations

from pathlib import Path

from unimatrix.config import load_config
from unimatrix.graphs import GRAPH_NAMES, GraphRenderer
from unimatrix.persistence import RunStore


def test_graph_render_smoke(stub_config: Path, tmp_path: Path) -> None:
    cfg = load_config(stub_config)
    db = tmp_path / "g.db"
    store = RunStore(db)
    # Seed a couple of agents and one conversation worth of messages.
    for spec in cfg.agents:
        store.upsert_agent({
            "agent_id": spec.id, "name": spec.name, "gender": spec.gender,
            "role": spec.role_initial, "class": spec.class_initial,
            "personality": spec.personality.model_dump(),
            "values": dict(spec.values), "backstory": spec.backstory,
            "social_need": 80.0, "state": "idle", "current_conversation_id": None,
        })
        store.record_status_change(spec.id, "initial", None, spec.role_initial)
        store.record_status_change(spec.id, "initial", None, spec.class_initial)

    a, b = cfg.agents[0].id, cfg.agents[1].id
    cid = store.open_conversation("1to1", a, [a, b])
    store.append_message(cid, 1, a, "Hello.")
    store.append_message(cid, 2, b, "Hi.")
    store.close_conversation(cid, "natural", "Greeting.")

    pid = store.open_proposal(a, b, "role", cfg.agents[1].role_initial, "scholar")
    store.record_vote(pid, a, "yes", "yes")
    store.record_vote(pid, b, "no", "no")
    store.close_proposal(pid, "rejected", 1, 1)

    out = tmp_path / "graphs"
    renderer = GraphRenderer(cfg, store, out)
    rendered = renderer.render_all()
    for name in GRAPH_NAMES:
        assert rendered[name].exists()
        assert rendered[name].stat().st_size > 0
    store.close()
