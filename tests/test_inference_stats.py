"""Inference observability: per-call timing, token usage, live gauges, and the
decision-prompt static prefix that enables llama.cpp prompt-cache reuse.

The stats path is exercised with httpx.MockTransport so the REAL _http_generate
(including usage/timings parsing) runs without a server.
"""
from __future__ import annotations

import httpx
import pytest

from unimatrix.agents.prompts import PromptBuilder
from unimatrix.agents.runtime import Agent
from unimatrix.config import InferenceConfig, load_config
from unimatrix.inference import ChatMessage, GenerationRequest, InferenceClient


def _req(label: str | None = None) -> GenerationRequest:
    return GenerationRequest(messages=[ChatMessage("user", "hi")], label=label)


def _mock_client(handler) -> InferenceClient:
    client = InferenceClient(
        InferenceConfig(
            backend="vllm", endpoint="http://unused", max_concurrent_requests=4
        )
    )
    # Swap the auto-created httpx client for one backed by a mock transport.
    client._client = httpx.AsyncClient(
        base_url="http://unused", transport=httpx.MockTransport(handler)
    )
    return client


@pytest.mark.asyncio
async def test_window_stats_capture_usage_and_timings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"action":"do_nothing"}'}}],
                "usage": {"prompt_tokens": 2900, "completion_tokens": 1500},
                "timings": {"prompt_ms": 800.0, "predicted_ms": 4200.0},
            },
        )

    client = _mock_client(handler)
    out = await client.generate_batch([_req() for _ in range(10)])
    assert len(out) == 10

    stats = client.window_stats()
    assert stats["calls"] == 10
    assert stats["avg_prompt_tokens"] == 2900
    assert stats["avg_completion_tokens"] == 1500
    assert stats["prefill_s"] == 8.0   # 10 × 800ms
    assert stats["decode_s"] == 42.0   # 10 × 4200ms
    assert 0 < stats["peak_inflight"] <= 4
    assert stats["errors"] == 0

    # Live gauges return to rest once the batch drains.
    live = client.inflight_snapshot()
    assert live["queued"] == 0
    assert live["inflight"] == 0
    assert live["cap"] == 4

    # Reset clears the window but keeps the cap.
    client.reset_window()
    assert client.window_stats()["calls"] == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_stats_survive_missing_usage_and_count_errors() -> None:
    # A backend that omits usage/timings (and 500s) must not break stats.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = _mock_client(handler)
    with pytest.raises(RuntimeError):
        await client.generate(_req("agent_x"))

    stats = client.window_stats()
    assert stats["calls"] == 1
    assert stats["errors"] == 1
    assert stats["avg_prompt_tokens"] == 0  # no usage reported, no divide-by-zero
    assert client.inflight_snapshot()["inflight"] == 0  # gauge released on error
    await client.aclose()


def test_decision_prompt_leads_with_static_world_rules(stub_config) -> None:
    cfg = load_config(stub_config)
    pb = PromptBuilder(cfg)
    agents = [Agent.from_spec(s, cfg.social.social_need_initial) for s in cfg.agents[:2]]

    rules = pb.world_rules_block()
    sys_a = pb.decide_action_messages(agents[0], agents, [], [], [], False, [])[0][0].content
    sys_b = pb.decide_action_messages(agents[1], agents, [], [], [], False, [])[0][0].content

    # The static world-rules block leads the system prompt → a shared, cacheable
    # prefix identical across agents (and across ticks).
    assert sys_a.startswith(rules)
    assert sys_b.startswith(rules)
    assert sys_a[: len(rules)] == sys_b[: len(rules)]
    # ...but the per-agent identity still differs right after that prefix.
    assert sys_a != sys_b
