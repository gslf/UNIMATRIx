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
from unimatrix.economy import EconomyModule
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

    # Pick a target whose role is NOT protected, so the vote can actually
    # open (the protected-role constraint blocks votes that would empty a
    # role like banker/supreme_judge/president when only one holder exists).
    protected = set(cfg.economy.protected_roles)
    target = next(
        a for a in agents.values() if a.role not in protected
    )
    target_id = target.id
    new_role = "scholar" if target.role != "scholar" else "doctor"

    proposal = await voting.open(
        proposer_id=next(a.id for a in agents.values() if a.id != target_id),
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

    protected = set(cfg.economy.protected_roles)
    target = next(a for a in agents.values() if a.role not in protected)
    target_id = target.id
    new_role = "scholar" if target.role != "scholar" else "doctor"

    proposal = await voting.open(
        proposer_id=next(a.id for a in agents.values() if a.id != target_id),
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


@pytest.mark.asyncio
async def test_protected_role_constraint_blocks_propose(
    stub_config: Path, tmp_path: Path
) -> None:
    """A vote that would empty out a protected role is rejected at propose()."""
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
    # Force exactly one banker in the test population by demoting the rest.
    bankers = [a for a in agents.values() if a.role == "banker"]
    assert bankers, "test fixture must include at least one banker"
    sole_banker = bankers[0]
    for extra in bankers[1:]:
        extra.role = "scholar"
    for ag in agents.values():
        store.upsert_agent(ag.to_db_row())

    voting = VotingModule(cfg, store, memory, inference,
                          PromptBuilder(cfg), agents, Console())

    proposer_id = next(a.id for a in agents.values() if a.id != sole_banker.id)
    ok, reason = await voting.propose(
        proposer_id=proposer_id,
        target_id=sole_banker.id,
        change_type="role",
        to_value="scholar",
    )
    assert ok is False
    assert "cannot remove last" in reason
    assert "banker" in reason

    # Proposing a change for a NON-protected role should still pass.
    non_protected = next(
        a for a in agents.values()
        if a.role not in cfg.economy.protected_roles and a.id != proposer_id
    )
    new_role = "scholar" if non_protected.role != "scholar" else "doctor"
    ok2, reason2 = await voting.propose(
        proposer_id=proposer_id,
        target_id=non_protected.id,
        change_type="role",
        to_value=new_role,
    )
    assert ok2 is True, reason2

    await inference.aclose()
    await memory.close()
    store.close()


@pytest.mark.asyncio
async def test_money_prize_vote_pays_target(
    stub_config: Path, tmp_path: Path
) -> None:
    """Approved money_prize moves funds from community to target."""
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

    econ = EconomyModule(cfg, store, agents, Console())
    await econ.initialize()
    voting = VotingModule(cfg, store, memory, inference,
                          PromptBuilder(cfg), agents, Console())
    voting.economy = econ

    target_id = list(agents.keys())[1]
    pre_target = agents[target_id].bank_account
    pre_community = store.get_community_balance()

    proposal = await voting.open(
        proposer_id=list(agents.keys())[0],
        target_id=target_id,
        change_type="money_prize",
        to_value="42",
        motivation="for outstanding service",
    )
    assert proposal is not None
    result = await voting.collect_and_close()

    if result["outcome"] == "approved":
        # Funds moved.
        assert agents[target_id].bank_account == pytest.approx(pre_target + 42.0)
        assert store.get_community_balance() == pytest.approx(pre_community - 42.0)
        # And the prize counter ticked.
        assert econ.prizes_paid == 1
    else:
        # Stub vote rejected — no movement, no prize.
        assert agents[target_id].bank_account == pytest.approx(pre_target)
        assert store.get_community_balance() == pytest.approx(pre_community)
        assert econ.prizes_paid == 0

    await inference.aclose()
    await memory.close()
    store.close()
