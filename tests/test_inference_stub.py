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
async def test_stub_vote_returns_yes_or_no(stub_client: InferenceClient) -> None:
    raw = await stub_client.generate(GenerationRequest(
        messages=[ChatMessage("user", "vote")], stub_kind="vote",
    ))
    assert raw.strip() in ("yes", "no")


def test_parse_json_lenient_handles_noise() -> None:
    assert parse_json_lenient('here is the answer: {"vote": "yes"}.')["vote"] == "yes"
    assert parse_json_lenient("garbage") is None
