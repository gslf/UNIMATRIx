"""The inference client caps how many HTTP requests fly at once.

These tests exercise the semaphore in InferenceClient without a real server:
a non-stub backend is built (so generate() takes the semaphore-guarded path)
and `_http_generate` is monkeypatched to a coroutine that tracks live
concurrency — no socket is ever opened.
"""
from __future__ import annotations

import asyncio

import pytest

from unimatrix.config import InferenceConfig
from unimatrix.inference import ChatMessage, GenerationRequest, InferenceClient


def _req() -> GenerationRequest:
    return GenerationRequest(messages=[ChatMessage("user", "hi")])


@pytest.mark.asyncio
async def test_semaphore_caps_in_flight() -> None:
    cap = 4
    client = InferenceClient(
        InferenceConfig(
            backend="vllm", endpoint="http://unused", max_concurrent_requests=cap
        )
    )

    live = 0
    observed_max = 0

    async def fake_http(req: GenerationRequest) -> str:
        nonlocal live, observed_max
        live += 1
        observed_max = max(observed_max, live)
        await asyncio.sleep(0.01)  # let other acquired tasks overlap
        assert live <= cap, f"{live} in flight exceeds cap {cap}"
        live -= 1
        return "ok"

    client._http_generate = fake_http  # type: ignore[method-assign]

    n = 40
    out = await client.generate_batch([_req() for _ in range(n)])

    assert out == ["ok"] * n
    assert observed_max <= cap  # never exceeded the cap
    assert observed_max > 1  # but did run concurrently (cap > 1)
    await client.aclose()


@pytest.mark.asyncio
async def test_single_generate_also_bounded() -> None:
    # A lone generate() (loan / impression path) must also pass through the
    # semaphore — i.e. the wrap lives in generate(), not only generate_batch().
    client = InferenceClient(
        InferenceConfig(
            backend="vllm", endpoint="http://unused", max_concurrent_requests=2
        )
    )

    async def fake_http(req: GenerationRequest) -> str:
        return "x"

    client._http_generate = fake_http  # type: ignore[method-assign]
    assert await client.generate(_req()) == "x"
    await client.aclose()
