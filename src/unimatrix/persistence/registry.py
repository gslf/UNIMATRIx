"""Global registry of runs (one row per simulation run).

The registry is a tiny SQLite db at runs/_registry.db, separate from per-run
databases so that operations on individual runs (export, delete) cannot corrupt
the index.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    db_path       TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    status        TEXT NOT NULL,
    config_json   TEXT NOT NULL
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Registry:
    def __init__(self, runs_dir: str | Path = "runs") -> None:
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.runs_dir / "_registry.db"
        with self._conn() as c:
            c.executescript(REGISTRY_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def register(self, name: str, db_path: str, config: dict) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO runs (name, db_path, started_at, status, config_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, db_path, _utc_now(), "running", json.dumps(config)),
            )
            return int(cur.lastrowid or 0)

    def update_status(
        self, run_id: int, status: str, ended_at: str | None = None
    ) -> None:
        with self._conn() as c:
            if ended_at is not None:
                c.execute(
                    "UPDATE runs SET status = ?, ended_at = ? WHERE id = ?",
                    (status, ended_at, run_id),
                )
            else:
                c.execute(
                    "UPDATE runs SET status = ? WHERE id = ?", (status, run_id)
                )

    def list_runs(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, name, db_path, started_at, ended_at, status FROM runs "
                "ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get(self, run_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete(self, run_id: int) -> dict | None:
        info = self.get(run_id)
        if info is None:
            return None
        with self._conn() as c:
            c.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        return info
