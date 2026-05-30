from __future__ import annotations

import pytest

from unimatrix.config import InferenceConfig
from unimatrix.inference import ChatMessage, GenerationRequest, InferenceClient
from unimatrix.inference.client import parse_json_lenient


@pytest.fixture
def stub_client() -> InferenceClient:
    return InferenceClient(InferenceConfig(backend="stub", endpoint=""))


@pytest.mark.asyncio
async def test_stub_chat(stub_client: InferenceClient) -> None:
    out = await stub_client.generate(GenerationRequest(
        messages=[ChatMessage("system", "you are X"), ChatMessage("user", "hi")],
        stub_kind="chat",
    ))
    assert isinstance(out, str) and len(out) > 0


@pytest.mark.asyncio
async def test_stub_decision_returns_json(stub_client: InferenceClient) -> None:
    req = GenerationRequest(
        messages=[ChatMessage("system", "x"), ChatMessage("user", "respond with json")],
        stub_kind="decision",
        stub_context={
            "available_actions": ["do_nothing", "start_1to1", "start_group"],
            "idle_peers": ["a", "b"],
            "open_groups": [],
            "self_id": "me",
            "classes": ["people"],
            "roles": ["worker"],
        },
    )
    raw = await stub_client.generate(req)
    parsed = parse_json_lenient(raw)
    assert parsed and "action" in parsed


@pytest.mark.asyncio
async def test_stub_election_ballot_returns_candidate(stub_client: InferenceClient) -> None:
    raw = await stub_client.generate(GenerationRequest(
        messages=[ChatMessage("user", "vote for office")],
        stub_kind="election_ballot",
        stub_context={"candidates": ["agent_01", "agent_02", "agent_03"],
                      "office": "senator", "self_id": "agent_05"},
    ))
    assert raw.strip() in ("agent_01", "agent_02", "agent_03")


@pytest.mark.asyncio
async def test_stub_election_reassign_returns_role(stub_client: InferenceClient) -> None:
    raw = await stub_client.generate(GenerationRequest(
        messages=[ChatMessage("user", "reassign role")],
        stub_kind="election_reassign",
        stub_context={"roles": ["worker", "clerk", "scholar"], "target": "agent_01"},
    ))
    assert raw.strip() in ("worker", "clerk", "scholar")


@pytest.mark.asyncio
async def test_stub_decision_new_actions(stub_client: InferenceClient) -> None:
    """The decision stub must emit a valid action for the new catalog."""
    req = GenerationRequest(
        messages=[ChatMessage("user", "respond with json")],
        stub_kind="decision",
        stub_context={
            "available_actions": ["praise", "steal", "gift", "do_nothing"],
            "others": ["a", "b"],
            "idle_peers": ["a"],
            "officeholders": [],
            "bankers": [],
            "open_groups": [],
            "self_id": "me",
        },
    )
    raw = await stub_client.generate(req)
    parsed = parse_json_lenient(raw)
    assert parsed and parsed["action"] in (
        "praise", "steal", "gift", "do_nothing"
    )


@pytest.mark.asyncio
async def test_stub_decision_forced_emits_message(stub_client: InferenceClient) -> None:
    """When `forced` is set the stub must include a non-empty messages array
    (this is what keeps the anti-silence path meaningful)."""
    req = GenerationRequest(
        messages=[ChatMessage("user", "respond with json")],
        stub_kind="decision",
        stub_context={
            "available_actions": ["do_nothing"],
            "others": ["a", "b", "c"],
            "inbox_senders": ["b"],
            "forced": True,
            "self_id": "me",
        },
    )
    parsed = parse_json_lenient(await stub_client.generate(req),
                                want_keys=("messages", "action"))
    assert parsed is not None
    assert "action" in parsed
    assert isinstance(parsed.get("messages"), list) and parsed["messages"]
    first = parsed["messages"][0]
    assert isinstance(first.get("to"), list) and first["to"]
    assert isinstance(first.get("content"), str) and first["content"]


def test_parse_json_lenient_handles_noise() -> None:
    assert parse_json_lenient('here is the answer: {"vote": "yes"}.')["vote"] == "yes"
    assert parse_json_lenient("garbage") is None
