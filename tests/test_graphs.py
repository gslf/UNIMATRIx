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
    # Seed a couple of agents and a few messages between them.
    for spec in cfg.agents:
        store.upsert_agent({
            "agent_id": spec.id, "name": spec.name, "gender": spec.gender,
            "role": spec.role_initial, "class": spec.class_initial,
            "personality": spec.personality.model_dump(),
            "values": dict(spec.values), "backstory": spec.backstory,
            "social_need": 80.0, "state": "idle",
        })
        store.record_status_change(spec.id, "initial", None, spec.role_initial)
        store.record_status_change(spec.id, "initial", None, spec.class_initial)

    a, b = cfg.agents[0].id, cfg.agents[1].id
    store.add_message(a, [b], "Hello.", tick_no=1)
    store.add_message(b, [a], "Hi.", tick_no=2)

    pid = store.open_proposal(a, b, "role", cfg.agents[1].role_initial, "scholar")
    store.record_vote(pid, a, "yes", "yes")
    store.record_vote(pid, b, "no", "no")
    store.close_proposal(pid, "rejected", 1, 1)

    out = tmp_path / "graphs"
    renderer = GraphRenderer(cfg, store, out)
    rendered = renderer.render_all()
    # render_all only returns graphs that have data; every returned file must be
    # a real, non-empty PNG.
    for name, path in rendered.items():
        assert name in GRAPH_NAMES
        assert path.exists()
        assert path.stat().st_size > 0
    # The seeded data drives these graphs, so they must be present...
    for name in ("class_distribution", "message_volume", "voting_timeline"):
        assert name in rendered
    # ...while graphs with no underlying data are hidden (no placeholder PNG).
    # No economy transactions were seeded, so balances is unavailable.
    assert "balances" not in rendered
    store.close()
