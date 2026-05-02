"""In-memory chat-completion monitor for the inference server.

Wraps the OpenAI-compatible ASGI app with a middleware that:

  - records every POST /v1/chat/completions request and its response
    (streaming SSE *or* non-streaming JSON), assembling the assistant text,
    counting chunks, and timing the call;
  - serves a single-page browser UI at /monitor and JSON endpoints at
    /monitor/api/chats and /monitor/api/chats/{id}.

The recorder lives entirely in process memory — restarting the server
clears everything. There is intentionally no disk persistence.

Place this OUTERMOST in the middleware stack so the captured request body
reflects what clients actually sent (before any server-side rewrites such
as the reasoning-effort injector).
"""
from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatRecord:
    id: int
    started_at: float
    completed_at: float | None = None
    model: str = ""
    messages: list[dict] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    streaming: bool = False
    response_content: str = ""
    response_reasoning: str = ""
    finish_reason: str | None = None
    chunk_count: int = 0
    status_code: int = 0
    error: str | None = None
    client: str = ""

    def latency_ms(self) -> int | None:
        if self.completed_at is None:
            return None
        return int((self.completed_at - self.started_at) * 1000)

    def status(self) -> str:
        if self.error:
            return "error"
        if self.completed_at is None:
            return "in_progress"
        return "completed"

    def to_summary(self) -> dict:
        first_user = next(
            (m.get("content") for m in self.messages if m.get("role") == "user"),
            None,
        )
        return {
            "id": self.id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "latency_ms": self.latency_ms(),
            "model": self.model,
            "streaming": self.streaming,
            "status": self.status(),
            "first_user": _summarize(first_user, 140),
            "preview": _summarize(self.response_content, 180),
            "n_messages": len(self.messages),
            "chunk_count": self.chunk_count,
            "client": self.client,
        }

    def to_detail(self) -> dict:
        return {
            "id": self.id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "latency_ms": self.latency_ms(),
            "model": self.model,
            "streaming": self.streaming,
            "messages": self.messages,
            "params": self.params,
            "response_content": self.response_content,
            "response_reasoning": self.response_reasoning,
            "finish_reason": self.finish_reason,
            "chunk_count": self.chunk_count,
            "status_code": self.status_code,
            "error": self.error,
            "status": self.status(),
            "client": self.client,
        }


def _summarize(content: Any, n: int) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        s = " ".join(parts)
    else:
        s = str(content)
    s = " ".join(s.split())
    if len(s) > n:
        return s[:n].rstrip() + "…"
    return s


class ChatMonitor:
    """ASGI middleware. See module docstring."""

    def __init__(self, app, server_started_at: float | None = None) -> None:
        self.app = app
        self._records: list[ChatRecord] = []
        self._counter = itertools.count(1)
        self.server_started_at = server_started_at or time.time()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")

        if path in ("/monitor", "/monitor/"):
            await _send_html(send, _INDEX_HTML)
            return
        if path == "/monitor/api/chats":
            await self._handle_list(scope, send)
            return
        if path.startswith("/monitor/api/chats/"):
            try:
                cid = int(path.rsplit("/", 1)[-1])
            except ValueError:
                await _send_json(send, {"error": "bad id"}, status=400)
                return
            await self._handle_detail(send, cid)
            return

        if method == "POST" and path.endswith("/chat/completions"):
            await self._capture(scope, receive, send)
            return

        await self.app(scope, receive, send)

    async def _handle_list(self, scope, send) -> None:
        qs = scope.get("query_string", b"").decode("latin-1")
        since = 0
        for kv in qs.split("&"):
            if kv.startswith("since="):
                try:
                    since = int(kv.split("=", 1)[1])
                except ValueError:
                    pass
        items = [r.to_summary() for r in self._records if r.id > since]
        max_id = self._records[-1].id if self._records else 0
        await _send_json(send, {
            "chats": items,
            "max_id": max_id,
            "total": len(self._records),
            "server_started_at": self.server_started_at,
        })

    async def _handle_detail(self, send, cid: int) -> None:
        for r in self._records:
            if r.id == cid:
                await _send_json(send, r.to_detail())
                return
        await _send_json(send, {"error": "not found"}, status=404)

    async def _capture(self, scope, receive, send) -> None:
        body = b""
        more = True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)

        try:
            data = json.loads(body or b"{}")
        except (ValueError, TypeError):
            data = None

        record = ChatRecord(id=next(self._counter), started_at=time.time())
        record.client = self._extract_client(scope)
        if isinstance(data, dict):
            messages = data.get("messages")
            if isinstance(messages, list):
                record.messages = messages
            record.model = str(data.get("model") or "")
            record.streaming = bool(data.get("stream", False))
            record.params = {k: v for k, v in data.items() if k != "messages"}
        self._records.append(record)

        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        sse_buffer = b""
        flat_buffer = b""

        async def wrapped_send(message):
            nonlocal sse_buffer, flat_buffer
            mt = message.get("type")
            if mt == "http.response.start":
                record.status_code = int(message.get("status", 0) or 0)
            elif mt == "http.response.body":
                chunk = message.get("body", b"") or b""
                more_body = bool(message.get("more_body", False))
                is_streaming = record.streaming and record.status_code == 200
                if is_streaming:
                    sse_buffer += chunk
                    sse_buffer = self._consume_sse(sse_buffer, record)
                else:
                    flat_buffer += chunk
                    if not more_body:
                        self._consume_json(flat_buffer, record)
                if not more_body:
                    record.completed_at = time.time()
                    if record.status_code >= 400 and not record.error:
                        record.error = f"HTTP {record.status_code}"
            await send(message)

        try:
            await self.app(scope, replay, wrapped_send)
        except Exception as exc:
            record.error = repr(exc)
            record.completed_at = time.time()
            raise

    @staticmethod
    def _extract_client(scope) -> str:
        client = scope.get("client") or ()
        host = client[0] if client else ""
        port = client[1] if len(client) > 1 else ""
        if host and port:
            return f"{host}:{port}"
        return host or ""

    def _consume_sse(self, buf: bytes, record: ChatRecord) -> bytes:
        out = buf
        while True:
            crlf = out.find(b"\r\n\r\n")
            lf = out.find(b"\n\n")
            if crlf < 0 and lf < 0:
                return out
            if crlf < 0:
                sep, sep_len = lf, 2
            elif lf < 0:
                sep, sep_len = crlf, 4
            else:
                if crlf < lf:
                    sep, sep_len = crlf, 4
                else:
                    sep, sep_len = lf, 2
            event = out[:sep]
            out = out[sep + sep_len:]
            for line in event.replace(b"\r\n", b"\n").split(b"\n"):
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == b"[DONE]":
                    continue
                try:
                    obj = json.loads(payload)
                except ValueError:
                    continue
                record.chunk_count += 1
                self._merge_chunk(obj, record)

    @staticmethod
    def _merge_chunk(obj: dict, record: ChatRecord) -> None:
        if not isinstance(obj, dict):
            return
        choices = obj.get("choices") or []
        if not choices:
            err = obj.get("error")
            if err is not None:
                record.error = err if isinstance(err, str) else json.dumps(err)
            return
        ch = choices[0]
        delta = ch.get("delta") or {}
        c = delta.get("content")
        if isinstance(c, str):
            record.response_content += c
        rc = delta.get("reasoning_content")
        if isinstance(rc, str):
            record.response_reasoning += rc
        fr = ch.get("finish_reason")
        if fr:
            record.finish_reason = fr

    @staticmethod
    def _consume_json(body: bytes, record: ChatRecord) -> None:
        if not body:
            return
        try:
            obj = json.loads(body)
        except (ValueError, TypeError):
            return
        if not isinstance(obj, dict):
            return
        if "error" in obj and not obj.get("choices"):
            err = obj["error"]
            record.error = err if isinstance(err, str) else json.dumps(err)
            return
        choices = obj.get("choices") or []
        if not choices:
            return
        ch = choices[0]
        msg = ch.get("message") or {}
        c = msg.get("content")
        if isinstance(c, str):
            record.response_content = c
        rc = msg.get("reasoning_content")
        if isinstance(rc, str):
            record.response_reasoning = rc
        fr = ch.get("finish_reason")
        if fr:
            record.finish_reason = fr


async def _send_json(send, obj, status: int = 200) -> None:
    body = json.dumps(obj, default=str).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def _send_html(send, html: str) -> None:
    body = html.encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/html; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


_INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Inference Monitor</title>
<style>
  *,*::before,*::after { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; font-family: -apple-system, "Segoe UI", system-ui, sans-serif; background: #0e0f12; color: #d6d8de; }
  header { display: flex; align-items: center; gap: 14px; padding: 0 16px; background: #16181d; border-bottom: 1px solid #23262e; height: 46px; }
  header h1 { font-size: 13px; font-weight: 600; margin: 0; letter-spacing: 0.04em; text-transform: uppercase; color: #b1b5be; }
  header .badge { padding: 2px 9px; border-radius: 10px; background: #23262e; font-size: 11px; color: #d6d8de; font-variant-numeric: tabular-nums; }
  header .meta { font-size: 11px; color: #8a8f9a; font-variant-numeric: tabular-nums; }
  header .pulse { width: 8px; height: 8px; border-radius: 50%; background: #3fb950; box-shadow: 0 0 8px #3fb950; }
  header .pulse.stale { background: #f85149; box-shadow: 0 0 8px #f85149; }
  main { display: grid; grid-template-columns: 380px 1fr; height: calc(100% - 46px); }
  .list { border-right: 1px solid #23262e; overflow-y: auto; }
  .list .empty { padding: 32px 18px; color: #5a5f6a; font-size: 13px; text-align: center; }
  .list .item { padding: 10px 14px; border-bottom: 1px solid #1a1c22; cursor: pointer; border-left: 3px solid transparent; }
  .list .item:hover { background: #14161b; }
  .list .item.selected { background: #1a1f2c; border-left-color: #58a6ff; }
  .list .item .top { display: flex; gap: 8px; align-items: center; font-size: 11px; color: #8a8f9a; margin-bottom: 5px; font-variant-numeric: tabular-nums; }
  .list .item .id { color: #58a6ff; font-weight: 600; }
  .list .item .first { font-size: 13px; color: #d6d8de; line-height: 1.4; max-height: 2.8em; overflow: hidden; }
  .list .item .preview { font-size: 11px; color: #8a8f9a; margin-top: 4px; max-height: 1.4em; overflow: hidden; }
  .list .item .status { margin-left: auto; font-size: 10px; padding: 1px 6px; border-radius: 5px; background: #23262e; color: #8a8f9a; }
  .list .item .status.in_progress { background: #3a2c0a; color: #d4a017; }
  .list .item .status.error { background: #3a0e0e; color: #f85149; }
  .list .item .status.completed { background: #0a3a14; color: #3fb950; }
  .detail { overflow-y: auto; padding: 18px 26px 60px; }
  .detail .empty { color: #5a5f6a; font-size: 13px; text-align: center; margin-top: 35vh; }
  .detail h2 { font-size: 14px; margin: 0 0 14px 0; color: #d6d8de; font-weight: 600; }
  .detail h2 .model { color: #58a6ff; font-weight: 500; }
  .summary { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 16px; padding: 12px 14px; background: #16181d; border: 1px solid #23262e; border-radius: 6px; }
  .summary .cell .k { font-size: 10px; color: #8a8f9a; text-transform: uppercase; letter-spacing: 0.06em; }
  .summary .cell .v { font-size: 13px; color: #d6d8de; margin-top: 3px; font-variant-numeric: tabular-nums; }
  .msg { padding: 10px 14px; margin-bottom: 8px; border-radius: 6px; background: #16181d; border-left: 3px solid #23262e; }
  .msg .role { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: #8a8f9a; margin-bottom: 6px; font-weight: 600; }
  .msg .content { white-space: pre-wrap; font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: 12.5px; line-height: 1.55; word-break: break-word; }
  .msg.system { border-left-color: #6e7681; }
  .msg.user { border-left-color: #58a6ff; }
  .msg.assistant { border-left-color: #3fb950; background: #131a14; }
  .msg.tool { border-left-color: #d4a017; }
  .msg .reasoning { background: #1a1d23; padding: 8px 10px; border-radius: 4px; margin-top: 8px; font-size: 11.5px; color: #b1b5be; white-space: pre-wrap; font-family: ui-monospace, monospace; line-height: 1.55; }
  .msg .reasoning::before { content: "REASONING"; display: block; font-family: -apple-system, system-ui, sans-serif; font-size: 9px; color: #8a8f9a; letter-spacing: 0.12em; margin-bottom: 4px; font-weight: 600; }
  .params { background: #16181d; border: 1px solid #23262e; padding: 10px 12px; border-radius: 6px; margin-bottom: 14px; font-family: ui-monospace, monospace; font-size: 11.5px; color: #b1b5be; white-space: pre-wrap; }
  .error-box { background: #2a1212; border: 1px solid #f85149; padding: 10px 14px; border-radius: 6px; color: #ff8585; margin-bottom: 14px; font-size: 12.5px; word-break: break-word; }
  .toggle { font-size: 11px; color: #58a6ff; cursor: pointer; user-select: none; margin: 8px 0; }
  .empty-msg { color: #5a5f6a; font-style: italic; }
  .section-title { font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: #6e7681; margin: 18px 0 8px; font-weight: 600; }
</style>
</head>
<body>
<header>
  <h1>Inference Monitor</h1>
  <span class="badge" id="count">0 chats</span>
  <span class="meta" id="server-up"></span>
  <div style="flex:1"></div>
  <span class="meta" id="status">connecting&hellip;</span>
  <span class="pulse" id="pulse" title="auto-refresh"></span>
</header>
<main>
  <div class="list" id="list"><div class="empty">waiting for first chat&hellip;</div></div>
  <div class="detail" id="detail"><div class="empty">Select a chat from the left to inspect its messages and response.</div></div>
</main>
<script>
const state = { selected: null, items: [], serverStartedAt: 0, lastOk: 0 };

function el(tag, attrs) {
  const e = document.createElement(tag);
  if (attrs) for (const k in attrs) {
    const v = attrs[k];
    if (v == null) continue;
    if (k === 'class') e.className = v;
    else if (k === 'onclick') e.onclick = v;
    else e.setAttribute(k, v);
  }
  for (let i = 2; i < arguments.length; i++) {
    const c = arguments[i];
    if (c == null) continue;
    e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return e;
}
function fmtTime(t) {
  if (!t) return '';
  const d = new Date(t * 1000);
  const hh = String(d.getHours()).padStart(2,'0');
  const mm = String(d.getMinutes()).padStart(2,'0');
  const ss = String(d.getSeconds()).padStart(2,'0');
  return hh+':'+mm+':'+ss;
}
function fmtMs(n) {
  if (n == null) return '—';
  return n >= 1000 ? (n/1000).toFixed(2)+'s' : n+'ms';
}
function fmtUptime(start) {
  if (!start) return '';
  const s = Math.max(0, Math.floor(Date.now()/1000 - start));
  if (s < 60) return 'up '+s+'s';
  if (s < 3600) return 'up '+Math.floor(s/60)+'m '+(s%60)+'s';
  return 'up '+Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';
}
function contentToText(c) {
  if (c == null) return '';
  if (typeof c === 'string') return c;
  if (Array.isArray(c)) {
    return c.map(p => {
      if (p && p.type === 'text') return p.text || '';
      if (typeof p === 'string') return p;
      return '['+(p && p.type || 'part')+']';
    }).join('\n');
  }
  return JSON.stringify(c, null, 2);
}

function renderList() {
  const list = document.getElementById('list');
  list.innerHTML = '';
  if (!state.items.length) {
    list.appendChild(el('div', { class: 'empty' }, 'waiting for first chat…'));
    return;
  }
  for (let i = state.items.length - 1; i >= 0; i--) {
    const it = state.items[i];
    const node = el('div', { class: 'item' + (state.selected === it.id ? ' selected' : '') });
    node.onclick = () => selectChat(it.id);
    const statusText = it.status === 'in_progress' ? '…'
      : it.status === 'error' ? 'err'
      : fmtMs(it.latency_ms);
    node.appendChild(el('div', { class: 'top' },
      el('span', { class: 'id' }, '#'+it.id),
      el('span', null, fmtTime(it.started_at)),
      it.model ? el('span', null, it.model) : null,
      el('span', { class: 'status '+it.status }, statusText),
    ));
    node.appendChild(el('div', { class: 'first' }, it.first_user || '(no user message)'));
    if (it.preview) node.appendChild(el('div', { class: 'preview' }, '→ '+it.preview));
    list.appendChild(node);
  }
  document.getElementById('count').textContent = state.items.length+' chats';
}

function renderDetail(d) {
  const det = document.getElementById('detail');
  det.innerHTML = '';
  if (!d) {
    det.appendChild(el('div', { class: 'empty' }, 'Select a chat from the left.'));
    return;
  }
  const heading = el('h2', null, 'Chat #'+d.id);
  if (d.model) {
    heading.appendChild(document.createTextNode(' — '));
    heading.appendChild(el('span', { class: 'model' }, d.model));
  }
  det.appendChild(heading);

  const sum = el('div', { class: 'summary' });
  function cell(k, v) { return el('div', { class: 'cell' }, el('div', { class: 'k' }, k), el('div', { class: 'v' }, v)); }
  sum.appendChild(cell('Status', d.status));
  sum.appendChild(cell('Latency', fmtMs(d.latency_ms)));
  sum.appendChild(cell('Started', fmtTime(d.started_at)));
  sum.appendChild(cell('Mode', d.streaming ? 'stream ('+d.chunk_count+' chunks)' : 'oneshot'));
  sum.appendChild(cell('Client', d.client || '—'));
  det.appendChild(sum);

  if (d.error) det.appendChild(el('div', { class: 'error-box' }, 'Error: '+d.error));

  if (d.params && Object.keys(d.params).length) {
    const tog = el('div', { class: 'toggle' }, '▸ request params');
    const box = el('pre', { class: 'params' }, JSON.stringify(d.params, null, 2));
    box.style.display = 'none';
    tog.onclick = () => {
      const open = box.style.display === 'none';
      box.style.display = open ? 'block' : 'none';
      tog.textContent = (open ? '▾' : '▸')+' request params';
    };
    det.appendChild(tog);
    det.appendChild(box);
  }

  det.appendChild(el('div', { class: 'section-title' }, 'Messages ('+(d.messages || []).length+')'));
  for (const m of (d.messages || [])) {
    const role = (m.role || 'unknown').toLowerCase();
    det.appendChild(el('div', { class: 'msg '+role },
      el('div', { class: 'role' }, role),
      el('div', { class: 'content' }, contentToText(m.content) || '(empty)'),
    ));
  }

  det.appendChild(el('div', { class: 'section-title' }, 'Response'));
  const resp = el('div', { class: 'msg assistant' });
  let respLabel = 'assistant';
  if (d.finish_reason) respLabel += ' · '+d.finish_reason;
  if (d.status === 'in_progress') respLabel += ' · streaming…';
  resp.appendChild(el('div', { class: 'role' }, respLabel));
  if (d.response_reasoning) {
    resp.appendChild(el('div', { class: 'reasoning' }, d.response_reasoning));
  }
  if (d.response_content) {
    resp.appendChild(el('div', { class: 'content' }, d.response_content));
  } else if (d.status === 'in_progress') {
    resp.appendChild(el('div', { class: 'content empty-msg' }, '(waiting for content…)'));
  } else {
    resp.appendChild(el('div', { class: 'content empty-msg' }, '(no content)'));
  }
  det.appendChild(resp);
}

async function selectChat(id) {
  state.selected = id;
  renderList();
  await refreshDetail();
}

async function refreshList() {
  const r = await fetch('/monitor/api/chats');
  const j = await r.json();
  state.items = j.chats || [];
  state.serverStartedAt = j.server_started_at || state.serverStartedAt;
  renderList();
}

async function refreshDetail() {
  if (state.selected == null) { renderDetail(null); return; }
  const r = await fetch('/monitor/api/chats/'+state.selected);
  if (!r.ok) return;
  const d = await r.json();
  renderDetail(d);
}

async function tick() {
  try {
    await refreshList();
    await refreshDetail();
    state.lastOk = Date.now();
    document.getElementById('status').textContent = 'live · '+new Date().toLocaleTimeString();
    document.getElementById('pulse').classList.remove('stale');
  } catch (e) {
    document.getElementById('status').textContent = 'disconnected';
    document.getElementById('pulse').classList.add('stale');
  }
  document.getElementById('server-up').textContent = fmtUptime(state.serverStartedAt);
  setTimeout(tick, 1000);
}
tick();
</script>
</body>
</html>
"""
