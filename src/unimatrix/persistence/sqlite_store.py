"""SQLite store for a single simulation run.

Holds the world's persistent state: the beings (agents) and their self-authored
identity history, the words they exchange, their memories and impressions, the
shared cultural commons, the spatial patches beings work, ties,
lineage and deaths, plus the public event log and checkpoints.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Iterator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_schema() -> str:
    return resources.files("unimatrix.persistence").joinpath("schema.sql").read_text(
        encoding="utf-8"
    )


class RunStore:
    """Thread-safe SQLite wrapper. All methods are synchronous; async callers
    wrap them in ``asyncio.to_thread``."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.executescript(_load_schema())
        self._migrate()

    def _migrate(self) -> None:
        """Additive, idempotent migrations: `CREATE TABLE IF NOT EXISTS` never
        adds columns to a pre-existing table, so bring older run DBs forward by
        adding any columns introduced after their schema was first written."""
        with self._lock:
            cols = {
                r["name"]
                for r in self._conn.execute("PRAGMA table_info(agents)").fetchall()
            }
            for name, ddl in (
                ("traits_json", "traits_json TEXT"),
                ("pos_x", "pos_x INTEGER DEFAULT 0"),
                ("pos_y", "pos_y INTEGER DEFAULT 0"),
            ):
                if name not in cols:
                    self._conn.execute(f"ALTER TABLE agents ADD COLUMN {ddl}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ----- beings -----

    def upsert_agent(self, agent: dict) -> None:
        with self._tx() as c:
            c.execute(
                """
                INSERT INTO agents
                  (agent_id, name, gender, circumstance, disposition,
                   self_model_json, self_model_version, vitality, alive,
                   sustenance, born_tick, parent_ids, traits_json,
                   pos_x, pos_y, social_need, state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    name = excluded.name,
                    gender = excluded.gender,
                    circumstance = excluded.circumstance,
                    disposition = excluded.disposition,
                    self_model_json = excluded.self_model_json,
                    self_model_version = excluded.self_model_version,
                    vitality = excluded.vitality,
                    alive = excluded.alive,
                    sustenance = excluded.sustenance,
                    born_tick = excluded.born_tick,
                    parent_ids = excluded.parent_ids,
                    traits_json = excluded.traits_json,
                    pos_x = excluded.pos_x,
                    pos_y = excluded.pos_y,
                    social_need = excluded.social_need,
                    state = excluded.state
                """,
                (
                    agent["agent_id"],
                    agent.get("name"),
                    agent.get("gender"),
                    agent.get("circumstance", ""),
                    agent.get("disposition", ""),
                    json.dumps(agent.get("self_model_json", {})),
                    int(agent.get("self_model_version", 0) or 0),
                    float(agent.get("vitality", 100.0) or 0.0),
                    int(bool(agent.get("alive", True))),
                    float(agent.get("sustenance", 0.0) or 0.0),
                    int(agent.get("born_tick", 0) or 0),
                    json.dumps(agent.get("parent_ids", []) or []),
                    json.dumps(agent.get("traits_json", {}) or {}),
                    int(agent.get("pos_x", 0) or 0),
                    int(agent.get("pos_y", 0) or 0),
                    float(agent.get("social_need", 100.0) or 0.0),
                    agent.get("state", "idle"),
                ),
            )

    def update_agent_state(
        self,
        agent_id: str,
        state: str | None = None,
        social_need: float | None = None,
        vitality: float | None = None,
        alive: bool | None = None,
        sustenance: float | None = None,
        self_model_json: dict | None = None,
        self_model_version: int | None = None,
        traits_json: dict | None = None,
        pos_x: int | None = None,
        pos_y: int | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if state is not None:
            sets.append("state = ?"); params.append(state)
        if social_need is not None:
            sets.append("social_need = ?"); params.append(float(social_need))
        if vitality is not None:
            sets.append("vitality = ?"); params.append(float(vitality))
        if alive is not None:
            sets.append("alive = ?"); params.append(int(bool(alive)))
        if sustenance is not None:
            sets.append("sustenance = ?"); params.append(float(sustenance))
        if self_model_json is not None:
            sets.append("self_model_json = ?"); params.append(json.dumps(self_model_json))
        if self_model_version is not None:
            sets.append("self_model_version = ?"); params.append(int(self_model_version))
        if traits_json is not None:
            sets.append("traits_json = ?"); params.append(json.dumps(traits_json))
        if pos_x is not None:
            sets.append("pos_x = ?"); params.append(int(pos_x))
        if pos_y is not None:
            sets.append("pos_y = ?"); params.append(int(pos_y))
        if not sets:
            return
        params.append(agent_id)
        with self._tx() as c:
            c.execute(
                f"UPDATE agents SET {', '.join(sets)} WHERE agent_id = ?", params
            )

    def get_agent(self, agent_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            return dict(row) if row else None

    # ----- patches (ecology mode) -----

    def init_patches(self, rows: Iterable[dict]) -> None:
        """Create the patch grid once, at world bootstrap. Idempotent: existing
        patches are left untouched (so a resume never resets the world)."""
        with self._tx() as c:
            for r in rows:
                c.execute(
                    "INSERT OR IGNORE INTO patches "
                    "(x, y, resource, capacity, regen, updated_tick) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (int(r["x"]), int(r["y"]), float(r["resource"]),
                     float(r["capacity"]), float(r["regen"]), int(r.get("tick", 0))),
                )

    def list_patches(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM patches ORDER BY y, x"
            ).fetchall()
            return [dict(r) for r in rows]

    def bulk_update_patch_resources(self, updates: Iterable[tuple]) -> None:
        """updates: iterable of (resource, tick, x, y)."""
        with self._tx() as c:
            c.executemany(
                "UPDATE patches SET resource = ?, updated_tick = ? "
                "WHERE x = ? AND y = ?",
                [(float(res), int(tick), int(x), int(y)) for res, tick, x, y in updates],
            )

    def list_agents(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agents ORDER BY agent_id"
            ).fetchall()
            return [dict(r) for r in rows]

    # ----- self-model history (the 'becoming' timeline) -----

    def add_self_model(
        self, agent_id: str, version: int, tick_no: int,
        self_model_json: dict, diff: str = "",
    ) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO self_models "
                "(agent_id, version, tick_no, self_model_json, diff, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (agent_id, int(version), int(tick_no),
                 json.dumps(self_model_json), diff or "", utc_now_iso()),
            )
            return int(cur.lastrowid or 0)

    def list_self_models(self, agent_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM self_models WHERE agent_id = ? "
                "ORDER BY version ASC, id ASC", (agent_id,),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["self_model"] = json.loads(d.get("self_model_json") or "{}")
            except (TypeError, ValueError):
                d["self_model"] = {}
            out.append(d)
        return out

    def self_model_ticks(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tick_no FROM self_models "
                "WHERE version > 0 AND tick_no IS NOT NULL ORDER BY tick_no"
            ).fetchall()
            return [int(r["tick_no"]) for r in rows]

    # ----- words (messages) -----

    @staticmethod
    def _msg_row(r: sqlite3.Row) -> dict:
        d = dict(r)
        rec = d.pop("recipients", None)
        d["recipients"] = rec.split(",") if rec else []
        return d

    def add_message(
        self, sender_id: str, recipient_ids: Iterable[str], content: str, tick_no: int
    ) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO messages (sender_id, content, ts, tick_no) "
                "VALUES (?, ?, ?, ?)",
                (sender_id, content, utc_now_iso(), tick_no),
            )
            mid = int(cur.lastrowid or 0)
            for rid in recipient_ids:
                c.execute(
                    "INSERT OR IGNORE INTO message_recipients "
                    "(message_id, recipient_id) VALUES (?, ?)", (mid, rid),
                )
            return mid

    def unread_messages_for(self, agent_id: str, after_tick: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id, m.sender_id, m.content, m.ts, m.tick_no, "
                "       group_concat(r2.recipient_id) AS recipients "
                "FROM messages m "
                "JOIN message_recipients r "
                "  ON r.message_id = m.id AND r.recipient_id = ? "
                "LEFT JOIN message_recipients r2 ON r2.message_id = m.id "
                "WHERE m.tick_no > ? GROUP BY m.id ORDER BY m.id",
                (agent_id, after_tick),
            ).fetchall()
            return [self._msg_row(r) for r in rows]

    def recent_messages_for(self, agent_id: str, limit: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id, m.sender_id, m.content, m.ts, m.tick_no, "
                "       group_concat(r2.recipient_id) AS recipients "
                "FROM messages m "
                "LEFT JOIN message_recipients r2 ON r2.message_id = m.id "
                "WHERE m.sender_id = ? OR m.id IN "
                "  (SELECT message_id FROM message_recipients WHERE recipient_id = ?) "
                "GROUP BY m.id ORDER BY m.id DESC LIMIT ?",
                (agent_id, agent_id, limit),
            ).fetchall()
            return [self._msg_row(r) for r in reversed(rows)]

    def messages_between(self, a_id: str, b_id: str, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id, m.sender_id, m.content, m.ts, m.tick_no, "
                "       group_concat(r.recipient_id) AS recipients "
                "FROM messages m LEFT JOIN message_recipients r "
                "  ON r.message_id = m.id "
                "WHERE (m.sender_id = ? AND m.id IN "
                "        (SELECT message_id FROM message_recipients WHERE recipient_id = ?)) "
                "   OR (m.sender_id = ? AND m.id IN "
                "        (SELECT message_id FROM message_recipients WHERE recipient_id = ?)) "
                "GROUP BY m.id ORDER BY m.id ASC LIMIT ?",
                (a_id, b_id, b_id, a_id, limit),
            ).fetchall()
            return [self._msg_row(r) for r in rows]

    # ----- memory: reflections + impressions -----

    def add_memory_summary(self, agent_id: str, summary: str) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO memory_summaries (agent_id, summary, ts) "
                "VALUES (?, ?, ?)", (agent_id, summary, utc_now_iso()),
            )
            return int(cur.lastrowid or 0)

    def recent_summaries(self, agent_id: str, limit: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_summaries WHERE agent_id = ? "
                "ORDER BY id DESC LIMIT ?", (agent_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def count_memory_summaries(self, agent_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) n FROM memory_summaries WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            return int(row["n"]) if row else 0

    def upsert_person_memory(
        self, observer_id: str, subject_id: str, impression: str
    ) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO person_memories "
                "(observer_id, subject_id, impression, last_updated) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(observer_id, subject_id) DO UPDATE SET "
                "impression = excluded.impression, last_updated = excluded.last_updated",
                (observer_id, subject_id, impression, utc_now_iso()),
            )

    def get_person_memory(self, observer_id: str, subject_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT impression FROM person_memories "
                "WHERE observer_id = ? AND subject_id = ?",
                (observer_id, subject_id),
            ).fetchone()
            return row["impression"] if row else None

    def list_person_memories_for(self, observer_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT subject_id, impression, last_updated FROM person_memories "
                "WHERE observer_id = ? ORDER BY last_updated DESC", (observer_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ----- meaning: cultural commons -----

    def add_artifact(
        self, author_id: str, kind: str, content: str, tick_no: int,
        parent_artifact_id: int | None = None,
    ) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO cultural_artifacts "
                "(author_id, kind, content, tick_no, parent_artifact_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (author_id, kind, content, int(tick_no), parent_artifact_id),
            )
            return int(cur.lastrowid or 0)

    def recent_artifacts(self, limit: int = 12) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM cultural_artifacts ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
            return [dict(r) for r in rows][::-1]

    def list_artifacts(self, limit: int = 1000) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM cultural_artifacts ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def record_adoption(
        self, agent_id: str, artifact_id: int, self_model_version: int, tick_no: int
    ) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO belief_adoptions "
                "(agent_id, artifact_id, self_model_version, tick_no) "
                "VALUES (?, ?, ?, ?)",
                (agent_id, int(artifact_id), int(self_model_version), int(tick_no)),
            )

    def list_adoptions(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM belief_adoptions ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    # ----- kinship: ties -----

    def upsert_relationship(
        self, observer_id: str, subject_id: str, type_: str,
        strength_delta: float, note: str, tick_no: int,
    ) -> None:
        with self._tx() as c:
            row = c.execute(
                "SELECT strength, history FROM relationships "
                "WHERE observer_id = ? AND subject_id = ?",
                (observer_id, subject_id),
            ).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO relationships "
                    "(observer_id, subject_id, type, strength, history, "
                    "formed_tick, updated_tick) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (observer_id, subject_id, type_, float(strength_delta),
                     note, int(tick_no), int(tick_no)),
                )
            else:
                new_strength = float(row["strength"] or 0) + float(strength_delta)
                hist = (row["history"] or "")
                hist = (hist + " | " + note)[-400:] if note else hist
                c.execute(
                    "UPDATE relationships SET type = ?, strength = ?, history = ?, "
                    "updated_tick = ?, dissolved_tick = NULL "
                    "WHERE observer_id = ? AND subject_id = ?",
                    (type_, new_strength, hist, int(tick_no), observer_id, subject_id),
                )

    def dissolve_relationship(
        self, observer_id: str, subject_id: str, tick_no: int
    ) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE relationships SET dissolved_tick = ? "
                "WHERE observer_id = ? AND subject_id = ?",
                (int(tick_no), observer_id, subject_id),
            )

    def get_relationship(self, observer_id: str, subject_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM relationships WHERE observer_id = ? AND subject_id = ?",
                (observer_id, subject_id),
            ).fetchone()
            return dict(row) if row else None

    def list_relationships(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM relationships ORDER BY observer_id, subject_id"
            ).fetchall()
            return [dict(r) for r in rows]

    def relationships_for(self, observer_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM relationships WHERE observer_id = ? "
                "AND dissolved_tick IS NULL ORDER BY strength DESC", (observer_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ----- mortality & continuity: lineage + memorials -----

    def record_lineage(
        self, child_id: str, parent_ids: list[str], inherited: dict, tick_no: int
    ) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO lineage "
                "(child_id, parent_ids, inherited_json, tick_no) VALUES (?, ?, ?, ?)",
                (child_id, json.dumps(parent_ids), json.dumps(inherited), int(tick_no)),
            )

    def list_lineage(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM lineage ORDER BY tick_no"
            ).fetchall()
            return [dict(r) for r in rows]

    def record_death(
        self, agent_id: str, cause: str, tick_no: int, legacy_summary: str
    ) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO deaths "
                "(agent_id, cause, tick_no, legacy_summary, ts) VALUES (?, ?, ?, ?, ?)",
                (agent_id, cause, int(tick_no), legacy_summary, utc_now_iso()),
            )

    def list_deaths(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM deaths ORDER BY tick_no"
            ).fetchall()
            return [dict(r) for r in rows]

    # ----- public events -----

    def record_event(self, event_type: str, payload: dict) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO public_events (event_type, payload, ts) VALUES (?, ?, ?)",
                (event_type, json.dumps(payload), utc_now_iso()),
            )
            return int(cur.lastrowid or 0)

    def recent_events(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM public_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return self._decode_events(reversed(rows))

    def events_by_type(self, event_type: str, limit: int = 2000) -> list[dict]:
        """Events of one kind, oldest-first — e.g. every `harvest` so far."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM public_events WHERE event_type = ? "
                "ORDER BY id DESC LIMIT ?", (event_type, limit),
            ).fetchall()
        return self._decode_events(reversed(rows))

    @staticmethod
    def _decode_events(rows) -> list[dict]:
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"])
            except (TypeError, json.JSONDecodeError):
                pass
            out.append(d)
        return out

    # ----- checkpoints -----

    def write_checkpoint(self, state: dict) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO checkpoints (ts, state) VALUES (?, ?)",
                (utc_now_iso(), json.dumps(state)),
            )
            return int(cur.lastrowid or 0)

    def latest_checkpoint(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM checkpoints ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["state"])
        except (TypeError, json.JSONDecodeError):
            return None
