"""End-to-end voting test using stub backend.

Bootstraps a tiny world (3 agents), opens a vote, runs collect_and_close,
verifies the proposal is closed, the votes are persisted, and (if approved)
the role is applied with a status_change recorded.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from unimatrix.agents import Agent
from unimatrix.config import load_config
from unimatrix.inference import InferenceClient
from unimatrix.memory import MemoryManager
from unimatrix.persistence import Registry, RunStore
from unimatrix.voting import VotingModule
from unimatrix.agents.prompts import PromptBuilder


@pytest.mark.asyncio
async def test_vote_flow(stub_config: Path, tmp_path: Path) -> None:
    cfg = load_config(stub_config)
    registry = Registry(tmp_path / "runs")
    store = RunStore(tmp_path / "test.db")
    run_id = registry.register("t", str(tmp_path / "test.db"), {})
    memory = MemoryManager(cfg.memory, store, run_id, str(tmp_path / "chroma"))
    inference = InferenceClient(cfg.inference)

    agents = {
        spec.id: Agent.from_spec(spec, cfg.social.social_need_initial)
        for spec in cfg.agents
    }
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    voting = VotingModule(cfg, store, memory, inference,
                          PromptBuilder(cfg), agents, Console())

    target_id = list(agents.keys())[1]
    target = agents[target_id]
    new_role = "scholar" if target.role != "scholar" else "doctor"

    proposal = await voting.open(
        proposer_id=list(agents.keys())[0],
        target_id=target_id,
        change_type="role",
        to_value=new_role,
    )
    assert proposal is not None
    # All agents are now in the voting state
    assert all(a.state.value == "voting" for a in agents.values())

    result = await voting.collect_and_close()
    assert result["proposal_id"] == proposal.id
    assert result["outcome"] in ("approved", "rejected")
    assert result["yes"] + result["no"] == len(agents)

    # Votes persisted
    votes = store.get_votes(proposal.id)
    assert len(votes) == len(agents)

    # If approved, role updated
    if result["outcome"] == "approved":
        assert agents[target_id].role == new_role
        # And a status_change row exists
        changes = store.list_status_changes()
        assert any(
            c["change_type"] == "role" and c["to_value"] == new_role
            and c["agent_id"] == target_id
            for c in changes
        )

    # All agents back to idle
    assert all(a.state.value == "idle" for a in agents.values())

    await inference.aclose()
    await memory.close()
    store.close()


@pytest.mark.asyncio
async def test_abort_vote_restores_idle_state(stub_config: Path, tmp_path: Path) -> None:
    cfg = load_config(stub_config)
    registry = Registry(tmp_path / "runs")
    store = RunStore(tmp_path / "test.db")
    run_id = registry.register("t", str(tmp_path / "test.db"), {})
    memory = MemoryManager(cfg.memory, store, run_id, str(tmp_path / "chroma"))
    inference = InferenceClient(cfg.inference)

    agents = {
        spec.id: Agent.from_spec(spec, cfg.social.social_need_initial)
        for spec in cfg.agents
    }
    for ag in agents.values():
        ag.current_conversation_id = 99
        store.upsert_agent(ag.to_db_row())

    voting = VotingModule(cfg, store, memory, inference,
                          PromptBuilder(cfg), agents, Console())

    target_id = list(agents.keys())[1]
    target = agents[target_id]
    new_role = "scholar" if target.role != "scholar" else "doctor"

    proposal = await voting.open(
        proposer_id=list(agents.keys())[0],
        target_id=target_id,
        change_type="role",
        to_value=new_role,
    )
    assert proposal is not None

    await voting.abort_active()
    assert voting.active is None
    assert all(a.state.value == "idle" for a in agents.values())
    assert all(a.current_conversation_id is None for a in agents.values())
    assert all(
        store.get_agent(a.id)["current_conversation_id"] is None
        for a in agents.values()
    )

    await inference.aclose()
    await memory.close()
    store.close()
