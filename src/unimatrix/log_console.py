"""Console subclass that mirrors `console.log` calls into a ring buffer.

Used by `main.py` so the web UI's "Recent events" panel can display the same
human-readable log lines that scroll past on the orchestrator terminal,
instead of a JSON dump of public_events.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from rich.console import Console
from rich.text import Text


class LoggingConsole(Console):
    """A Rich Console that also captures `log()` output as plain text.

    Keeps the last `buffer_size` lines in memory. Reading the buffer is
    cheap and thread-safe (the orchestrator runs in the asyncio loop while
    the web server reads via a threadpool).
    """

    def __init__(self, *args: Any, buffer_size: int = 200, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._log_buffer: deque[dict] = deque(maxlen=buffer_size)
        self._buf_lock = Lock()

    def log(self, *objects: Any, **kwargs: Any) -> None:
        sep = kwargs.get("sep", " ")
        self._capture(objects, sep)
        # `_stack_offset` controls which frame Rich attributes the log line to.
        # Default is 1 (the immediate caller of log). With our wrapper sitting
        # between the caller and Rich, bump to 2 so the file:line still points
        # at the real caller in terminal output.
        kwargs.setdefault("_stack_offset", 2)
        super().log(*objects, **kwargs)

    def _capture(self, objects: tuple[Any, ...], sep: str) -> None:
        parts: list[str] = []
        for obj in objects:
            if isinstance(obj, str):
                try:
                    parts.append(Text.from_markup(obj).plain)
                except Exception:
                    parts.append(obj)
            else:
                parts.append(str(obj))
        line = sep.join(parts).rstrip()
        if not line:
            return
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with self._buf_lock:
            self._log_buffer.append({"ts": ts, "text": line})

    def recent_log_lines(self, limit: int = 50) -> list[dict]:
        with self._buf_lock:
            data = list(self._log_buffer)
        if limit and len(data) > limit:
            return data[-limit:]
        return data
