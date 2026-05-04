"""HTTP inference client with transparent batching.

The vLLM backend talks to vLLM's OpenAI-compatible /v1/chat/completions endpoint;
batching is handled implicitly — vLLM merges concurrent in-flight requests on
its side. We just cap concurrency with a semaphore so we don't overwhelm it.

The llama_cpp backend talks to llama.cpp's /v1/chat/completions (same shape).

The stub backend produces deterministic pseudo-LLM output, useful for
orchestration tests and demos without a GPU.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from typing import Sequence

import httpx

from ..config import InferenceConfig


@dataclass
class ChatMessage:
    role: str  # 'system' | 'user' | 'assistant'
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class GenerationRequest:
    messages: list[ChatMessage]
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    # Hint the backend that JSON output is required. Used by the stub to format
    # output and by the vLLM backend (when supported) to enable guided JSON.
    json_mode: bool = False
    # Free-form tag for the stub to disambiguate output kinds.
    stub_kind: str | None = None
    # Free-form metadata available to the stub for deterministic output.
    stub_context: dict = field(default_factory=dict)


class InferenceClient:
    def __init__(self, cfg: InferenceConfig) -> None:
        self.cfg = cfg
        self._semaphore = asyncio.Semaphore(cfg.max_batch_size)
        self._client: httpx.AsyncClient | None = None
        if cfg.backend in ("vllm", "llama_cpp"):
            self._client = httpx.AsyncClient(
                base_url=cfg.endpoint, timeout=cfg.request_timeout_seconds
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def generate(self, req: GenerationRequest) -> str:
        async with self._semaphore:
            if self.cfg.backend == "stub":
                return _stub_generate(req)
            return await self._http_generate(req)

    async def generate_batch(
        self, reqs: Sequence[GenerationRequest]
    ) -> list[str]:
        # vLLM batches via concurrent HTTP requests; the semaphore caps fan-out.
        return await asyncio.gather(*(self.generate(r) for r in reqs))

    async def _http_generate(self, req: GenerationRequest) -> str:
        assert self._client is not None
        payload: dict = {
            "model": self.cfg.model,
            "messages": [m.to_dict() for m in req.messages],
            "max_tokens": req.max_tokens or self.cfg.max_tokens_per_message,
            "temperature": (
                req.temperature if req.temperature is not None else self.cfg.temperature
            ),
            "top_p": req.top_p if req.top_p is not None else self.cfg.top_p,
        }
        if req.json_mode:
            # Both backends accept response_format={'type': 'json_object'}.
            payload["response_format"] = {"type": "json_object"}
        try:
            r = await self._client.post("/v1/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"inference backend timeout after {self.cfg.request_timeout_seconds}s "
                f"(endpoint={self.cfg.endpoint}). The server may be overloaded; "
                f"try a smaller model, fewer agents, or raise request_timeout_seconds."
            ) from exc
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"cannot reach inference backend at {self.cfg.endpoint}: {exc}. "
                "Is the server running?"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"inference HTTP error against {self.cfg.endpoint}: {exc!r}"
            ) from exc
        if r.status_code >= 400:
            body = (r.text or "")[:500]
            raise RuntimeError(
                f"inference backend returned {r.status_code} from "
                f"{self.cfg.endpoint}/v1/chat/completions; "
                f"model='{self.cfg.model}'. Body: {body}"
            )
        try:
            data = r.json()
            content = data["choices"][0]["message"]["content"] or ""
            return strip_think_tags(strip_harmony_channels(content))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"unexpected response shape from {self.cfg.endpoint}: "
                f"{r.text[:300]!r}"
            ) from exc


# ---------------------------------------------------------------------------
# Stub backend
# ---------------------------------------------------------------------------

_STUB_OPENERS = [
    "Honestly,", "I think", "Listen,", "Well,", "Look,",
    "Frankly,", "From my view,", "If you ask me,", "Truly,",
]
_STUB_TOPICS = [
    "the new tax laws", "the recent reforms", "yesterday's gossip",
    "the rising prices", "the council's last decision", "the unrest in the south",
    "the old aristocracy", "the workers' demands", "the harvest",
]
_STUB_CLOSERS = [
    "but who knows.", "and I'll stand by that.", "though I may be wrong.",
    "we shall see.", "such is life.", "for what it's worth.",
]


def _seeded(req: GenerationRequest) -> random.Random:
    h = hashlib.sha256()
    for m in req.messages:
        h.update(m.role.encode())
        h.update(b"\x00")
        h.update(m.content.encode("utf-8", errors="replace"))
        h.update(b"\x01")
    h.update(json.dumps(req.stub_context, sort_keys=True).encode())
    return random.Random(int.from_bytes(h.digest()[:8], "big"))


def _stub_generate(req: GenerationRequest) -> str:
    rng = _seeded(req)
    kind = req.stub_kind or _infer_stub_kind(req)
    if kind == "decision":
        return _stub_decision(req, rng)
    if kind == "vote":
        return _stub_vote(req, rng)
    if kind == "summary":
        return _stub_summary(req, rng)
    if kind == "person_impression":
        return _stub_person_impression(req, rng)
    if kind == "debate":
        return _stub_debate(req, rng)
    return _stub_chat(req, rng)


def _stub_debate(req: GenerationRequest, rng: random.Random) -> str:
    return (
        f"{rng.choice(_STUB_OPENERS)} the proposed change "
        f"{rng.choice(['concerns me','seems fitting','warrants debate','should not pass'])} "
        f"{rng.choice(_STUB_CLOSERS)}"
    )


def _infer_stub_kind(req: GenerationRequest) -> str:
    text = "\n".join(m.content for m in req.messages).lower()
    if "respond with json" in text or '"action"' in text:
        if '"vote"' in text:
            return "vote"
        return "decision"
    if "summarize" in text or "summary" in text:
        return "summary"
    return "chat"


def _stub_decision(req: GenerationRequest, rng: random.Random) -> str:
    ctx = req.stub_context
    available = ctx.get("available_actions") or [
        "do_nothing", "start_1to1", "start_group", "broadcast"
    ]
    others = ctx.get("idle_peers") or []
    classes = ctx.get("classes") or []
    roles = ctx.get("roles") or []
    self_id = ctx.get("self_id")
    action = rng.choice(available)
    out: dict = {"action": action}
    if action == "start_1to1" and others:
        out["target"] = rng.choice(others)
        out["topic"] = rng.choice(_STUB_TOPICS)
    elif action == "start_group" and len(others) >= 2:
        size = min(len(others), rng.randint(2, 5))
        out["targets"] = rng.sample(others, size)
        out["topic"] = rng.choice(_STUB_TOPICS)
    elif action == "broadcast":
        out["message"] = (
            f"{rng.choice(_STUB_OPENERS)} we must reflect on "
            f"{rng.choice(_STUB_TOPICS)} {rng.choice(_STUB_CLOSERS)}"
        )
    elif action == "propose_vote" and others and (classes or roles):
        target = rng.choice([self_id] + others) if self_id else rng.choice(others)
        if rng.random() < 0.5 and roles:
            out["proposal"] = {
                "target": target,
                "change_type": "role",
                "to_value": rng.choice(roles),
                "motivation": "The order would be more just under this change.",
            }
        elif classes:
            out["proposal"] = {
                "target": target,
                "change_type": "class",
                "to_value": rng.choice(classes),
                "motivation": "The order would be more just under this change.",
            }
        else:
            out["action"] = "do_nothing"
    elif action == "join_group":
        groups = ctx.get("open_groups") or []
        if groups:
            out["conversation_id"] = rng.choice(groups)
        else:
            out["action"] = "do_nothing"
    return json.dumps(out)


def _stub_vote(req: GenerationRequest, rng: random.Random) -> str:
    return "yes" if rng.random() < 0.55 else "no"


def _stub_summary(req: GenerationRequest, rng: random.Random) -> str:
    return (
        f"{rng.choice(['A tense', 'A cordial', 'An odd'])} exchange about "
        f"{rng.choice(_STUB_TOPICS)}; opinions {rng.choice(['diverged', 'converged'])}."
    )


def _stub_person_impression(req: GenerationRequest, rng: random.Random) -> str:
    return rng.choice([
        "Generally trustworthy, though prone to flattery.",
        "Ambitious; I should remain cautious.",
        "Sincere and thoughtful — a possible ally.",
        "Distant and unreadable; intentions unclear.",
    ])


def _stub_chat(req: GenerationRequest, rng: random.Random) -> str:
    sentences = rng.randint(1, 3)
    parts = []
    for _ in range(sentences):
        parts.append(
            f"{rng.choice(_STUB_OPENERS)} {rng.choice(_STUB_TOPICS)} "
            f"{rng.choice(_STUB_CLOSERS)}"
        )
    return " ".join(parts)


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

# Reasoning models (Qwen/QwQ, DeepSeek-R1, etc.) emit chain-of-thought wrapped
# in <think>...</think>. Some chat templates also pre-inject the opening
# <think> tag, so the response can contain only </think> at the end of CoT.
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_THINK_CLOSE_ONLY_RE = re.compile(r"^.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think\s*>", re.IGNORECASE)


def strip_think_tags(raw: str) -> str:
    """Remove reasoning-model <think>...</think> chain-of-thought.

    Handles three cases:
      - Balanced <think>...</think> blocks are removed entirely.
      - A trailing/lone </think> with no opener (chat template injected the
        opener) drops everything up to and including </think>.
      - An open <think> with no closer (truncated output) returns "" — the
        model never reached the real reply.
    """
    if not raw:
        return raw
    out = _THINK_BLOCK_RE.sub("", raw)
    if _THINK_CLOSE_RE.search(out) and not _THINK_OPEN_RE.search(out):
        out = _THINK_CLOSE_ONLY_RE.sub("", out, count=1)
    elif _THINK_OPEN_RE.search(out) and not _THINK_CLOSE_RE.search(out):
        return ""
    return out.strip()


# gpt-oss / harmony-style channels — extract the final message body if present.
_HARMONY_FINAL_RE = re.compile(
    r"<\|channel\|>final<\|message\|>(.+?)(?:<\|end\||<\|return\||$)",
    re.DOTALL,
)
# Any harmony channel/message marker. Used to detect responses where only the
# analysis channel was emitted (no final) — those are model "thinking" leakage,
# not a real reply.
_HARMONY_ANY_MARKER_RE = re.compile(r"<\|(?:channel|message|start|end|return)\|>")


def strip_harmony_channels(raw: str) -> str:
    """Return the harmony 'final' channel body, or the input unchanged.

    Behavior:
      - If a 'final' channel marker is present, return its body (drops any
        preceding analysis/commentary channels).
      - If harmony markers are present but no 'final' channel exists, return
        an empty string — the model emitted only analysis/commentary, which
        is chain-of-thought, not a reply.
      - Otherwise return the input unchanged.

    Note: only helps when the inference backend lets the harmony special
    tokens reach the client. Builds that strip them server-side leak the
    raw analysis content with no markers to anchor on; this function cannot
    recover the final channel in that case.
    """
    if not raw:
        return raw
    m = _HARMONY_FINAL_RE.search(raw)
    if m:
        return m.group(1).strip()
    if _HARMONY_ANY_MARKER_RE.search(raw):
        return ""
    return raw
# Generic markdown code fence (```json ... ```), tolerant of language tag.
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _candidate_json_blobs(raw: str) -> list[str]:
    """Return one or more substrings of `raw` that might be JSON objects."""
    out: list[str] = []
    text = raw.strip()
    if not text:
        return out
    # 1) harmony "final" channel
    m = _HARMONY_FINAL_RE.search(text)
    if m:
        text = m.group(1).strip()
    # 2) markdown fenced blocks
    out.extend(_FENCE_RE.findall(text))
    # 3) every balanced top-level {...} we can find by tracking depth
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                out.append(text[start : i + 1])
                start = -1
    # 4) the whole string as a fallback (in case it IS JSON without braces noise)
    if text not in out:
        out.append(text)
    # de-dup, preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for c in out:
        if c not in seen:
            uniq.append(c)
            seen.add(c)
    return uniq


def parse_json_lenient(raw: str, want_keys: tuple[str, ...] = ()) -> dict | None:
    """Extract a JSON object from a noisy LLM response.

    `want_keys`: if given, prefer the candidate that contains any of these
    top-level keys. Lets us pick the action/decision JSON even when the model
    emitted a thinking blob with its own braces beforehand.
    """
    if not raw:
        return None
    candidates = _candidate_json_blobs(raw)
    parsed: list[dict] = []
    for c in candidates:
        try:
            v = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(v, dict):
            parsed.append(v)
    if not parsed:
        return None
    if want_keys:
        for v in parsed:
            if any(k in v for k in want_keys):
                return v
    return parsed[0]
