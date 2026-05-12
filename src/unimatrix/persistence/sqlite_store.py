"""Per-run SQLite store. Thread-safe via a single connection guarded by a lock.

All access is synchronous; the orchestrator wraps writes in `asyncio.to_thread`
when called from the async loop.
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
    """SQLite store for a single simulation run.

    A single connection is reused with a re-entrant lock; sqlite3 in
    serialized mode (the default) is safe to share across threads as long as
    only one statement runs at a time.
    """

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
        """Idempotent in-place migrations for older run databases."""
        cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(votes)").fetchall()
        }
        if "raw_response" not in cols:
            self._conn.execute("ALTER TABLE votes ADD COLUMN raw_response TEXT")
        agent_cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(agents)").fetchall()
        }
        if "bank_account" not in agent_cols:
            self._conn.execute(
                "ALTER TABLE agents ADD COLUMN bank_account REAL DEFAULT 0"
            )

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

    # ----- agents -----

    def upsert_agent(self, agent: dict) -> None:
        with self._tx() as c:
            c.execute(
                """
                INSERT INTO agents
                  (agent_id, name, gender, role, class, personality, values_json,
                   backstory, social_need, state, current_conversation_id,
                   bank_account)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    name = excluded.name,
                    gender = excluded.gender,
                    role = excluded.role,
                    class = excluded.class,
                    personality = excluded.personality,
                    values_json = excluded.values_json,
                    backstory = excluded.backstory,
                    social_need = excluded.social_need,
                    state = excluded.state,
                    current_conversation_id = excluded.current_conversation_id,
                    bank_account = excluded.bank_account
                """,
                (
                    agent["agent_id"],
                    agent.get("name"),
                    agent.get("gender"),
                    agent.get("role"),
                    agent.get("class"),
                    json.dumps(agent.get("personality", {})),
                    json.dumps(agent.get("values", {})),
                    agent.get("backstory", ""),
                    agent.get("social_need", 100.0),
                    agent.get("state", "idle"),
                    agent.get("current_conversation_id"),
                    agent.get("bank_account", 0.0),
                ),
            )

    def update_agent_state(
        self,
        agent_id: str,
        state: str | None = None,
        social_need: float | None = None,
        current_conversation_id: int | None | object = ...,
        role: str | None = None,
        klass: str | None = None,
        bank_account: float | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if state is not None:
            sets.append("state = ?")
            params.append(state)
        if social_need is not None:
            sets.append("social_need = ?")
            params.append(social_need)
        if current_conversation_id is not ...:
            sets.append("current_conversation_id = ?")
            params.append(current_conversation_id)
        if role is not None:
            sets.append("role = ?")
            params.append(role)
        if klass is not None:
            sets.append("class = ?")
            params.append(klass)
        if bank_account is not None:
            sets.append("bank_account = ?")
            params.append(bank_account)
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

    def list_agents(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agents ORDER BY agent_id"
            ).fetchall()
            return [dict(r) for r in rows]

    # ----- economy -----

    def init_community_account(self, initial_balance: float) -> bool:
        """Create the singleton community row if missing.

        Returns True if the row was just inserted, False if it already existed.
        Idempotent — never overwrites an existing balance.
        """
        with self._tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO community_account (id, balance) "
                "VALUES (1, ?)",
                (float(initial_balance),),
            )
            return (cur.rowcount or 0) > 0

    def get_community_balance(self) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT balance FROM community_account WHERE id = 1"
            ).fetchone()
            return float(row["balance"]) if row else 0.0

    def get_agent_balance(self, agent_id: str) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT bank_account FROM agents WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            return float(row["bank_account"]) if row else 0.0

    def apply_transaction(
        self,
        kind: str,
        from_party: str | None,
        to_party: str | None,
        amount: float,
        reason: str = "",
        ref_id: int | None = None,
    ) -> int:
        """Atomically record a transaction and move the funds.

        `from_party` / `to_party` are either an agent_id, the literal string
        'community', or None (for one-sided init/expense rows). Money flows
        from → to; if either side is None it is a sink/source.
        All balance mutations and the log row land in the same SQL transaction.
        """
        amt = float(amount)
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO transactions "
                "(ts, kind, from_party, to_party, amount, reason, ref_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (utc_now_iso(), kind, from_party, to_party, amt, reason, ref_id),
            )
            tx_id = int(cur.lastrowid or 0)
            if from_party == "community":
                c.execute(
                    "UPDATE community_account SET balance = balance - ? "
                    "WHERE id = 1",
                    (amt,),
                )
            elif from_party is not None:
                c.execute(
                    "UPDATE agents SET bank_account = bank_account - ? "
                    "WHERE agent_id = ?",
                    (amt, from_party),
                )
            if to_party == "community":
                c.execute(
                    "UPDATE community_account SET balance = balance + ? "
                    "WHERE id = 1",
                    (amt,),
                )
            elif to_party is not None:
                c.execute(
                    "UPDATE agents SET bank_account = bank_account + ? "
                    "WHERE agent_id = ?",
                    (amt, to_party),
                )
            return tx_id

    def list_transactions(
        self,
        limit: int = 200,
        agent_id: str | None = None,
    ) -> list[dict]:
        with self._lock:
            if agent_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM transactions ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM transactions "
                    "WHERE from_party = ? OR to_party = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (agent_id, agent_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

    # ----- conversations -----

    def open_conversation(
        self, type_: str, initiator_id: str, participants: Iterable[str]
    ) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO conversations "
                "(type, initiator_id, participants, started_at) "
                "VALUES (?, ?, ?, ?)",
                (type_, initiator_id, json.dumps(list(participants)), utc_now_iso()),
            )
            return int(cur.lastrowid or 0)

    def close_conversation(
        self, conv_id: int, end_reason: str, summary: str | None = None
    ) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE conversations SET ended_at = ?, end_reason = ?, summary = ? "
                "WHERE id = ?",
                (utc_now_iso(), end_reason, summary, conv_id),
            )

    def update_conversation_participants(
        self, conv_id: int, participants: Iterable[str]
    ) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE conversations SET participants = ? WHERE id = ?",
                (json.dumps(list(participants)), conv_id),
            )

    def append_message(
        self, conv_id: int, turn_index: int, sender_id: str, content: str
    ) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO messages "
                "(conversation_id, turn_index, sender_id, content, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (conv_id, turn_index, sender_id, content, utc_now_iso()),
            )
            return int(cur.lastrowid or 0)

    def get_conversation(self, conv_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conv_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_messages(self, conv_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? "
                "ORDER BY turn_index ASC",
                (conv_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_conversations(self, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id, c.type, c.initiator_id, c.participants, "
                "       c.started_at, c.ended_at, c.end_reason, c.summary, "
                "       (SELECT COUNT(*) FROM messages m "
                "          WHERE m.conversation_id = c.id) AS message_count "
                "FROM conversations c "
                "ORDER BY c.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["participants"] = json.loads(d["participants"])
                except (TypeError, json.JSONDecodeError):
                    d["participants"] = []
                out.append(d)
            return out

    # ----- voting -----

    def open_proposal(
        self,
        proposer_id: str,
        target_id: str,
        change_type: str,
        from_value: str,
        to_value: str,
        motivation: str = "",
    ) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO vote_proposals "
                "(proposer_id, target_id, change_type, from_value, to_value, "
                " motivation, proposed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    proposer_id,
                    target_id,
                    change_type,
                    from_value,
                    to_value,
                    motivation,
                    utc_now_iso(),
                ),
            )
            return int(cur.lastrowid or 0)

    def record_vote(
        self,
        proposal_id: int,
        voter_id: str,
        vote: str,
        motivation: str,
        raw_response: str | None = None,
    ) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO votes "
                "(proposal_id, voter_id, vote, motivation, raw_response, voted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    proposal_id,
                    voter_id,
                    vote,
                    motivation,
                    raw_response,
                    utc_now_iso(),
                ),
            )

    def close_proposal(
        self,
        proposal_id: int,
        outcome: str,
        yes_count: int,
        no_count: int,
    ) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE vote_proposals SET outcome = ?, yes_count = ?, no_count = ?, "
                "closed_at = ? WHERE id = ?",
                (outcome, yes_count, no_count, utc_now_iso(), proposal_id),
            )

    def get_votes_by_voter(self, voter_id: str, limit: int = 10) -> list[dict]:
        """Return the most recent votes cast by a single voter, joined with
        the corresponding proposal so the UI gets target/change/outcome."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT v.proposal_id, v.vote, v.motivation, v.raw_response, "
                "       v.voted_at, p.target_id, p.change_type, p.from_value, "
                "       p.to_value, p.outcome "
                "FROM votes v JOIN vote_proposals p "
                "  ON v.proposal_id = p.id "
                "WHERE v.voter_id = ? "
                "ORDER BY v.proposal_id DESC LIMIT ?",
                (voter_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_votes(self, proposal_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM votes WHERE proposal_id = ?", (proposal_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def list_proposals(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM vote_proposals ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_proposal(self, proposal_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM vote_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            return dict(row) if row else None

    def events_by_type(
        self, types: list[str], limit: int = 500
    ) -> list[dict]:
        """Return public_events filtered by type, newest first."""
        if not types:
            return []
        placeholders = ",".join(["?"] * len(types))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM public_events "
                f"WHERE event_type IN ({placeholders}) "
                f"ORDER BY id DESC LIMIT ?",
                (*types, limit),
            ).fetchall()
            out: list[dict] = []
            for r in rows:
                d = dict(r)
                try:
                    d["payload"] = json.loads(d["payload"])
                except (TypeError, json.JSONDecodeError):
                    pass
                out.append(d)
            return out

    def debate_speeches_for(self, proposal_id: int) -> list[dict]:
        """Reassemble the pre-vote debate transcript for a proposal."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, payload, ts FROM public_events "
                "WHERE event_type = 'vote_debate_speech' "
                "ORDER BY id ASC"
            ).fetchall()
            out: list[dict] = []
            for r in rows:
                try:
                    payload = json.loads(r["payload"]) if r["payload"] else {}
                except (TypeError, json.JSONDecodeError):
                    continue
                if payload.get("proposal_id") != proposal_id:
                    continue
                out.append({
                    "round": payload.get("round"),
                    "speaker_id": payload.get("speaker_id"),
                    "speaker_name": payload.get("speaker_name"),
                    "text": payload.get("text"),
                    "ts": r["ts"],
                })
            return out

    # ----- status changes -----

    def record_status_change(
        self,
        agent_id: str,
        change_type: str,
        from_value: str | None,
        to_value: str,
        proposal_id: int | None = None,
    ) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO status_changes "
                "(agent_id, change_type, from_value, to_value, proposal_id, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (agent_id, change_type, from_value, to_value, proposal_id, utc_now_iso()),
            )

    def list_status_changes(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM status_changes ORDER BY ts ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ----- memory -----

    def add_memory_summary(
        self, agent_id: str, conversation_id: int | None, summary: str
    ) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO memory_summaries "
                "(agent_id, conversation_id, summary, ts) VALUES (?, ?, ?, ?)",
                (agent_id, conversation_id, summary, utc_now_iso()),
            )
            return int(cur.lastrowid or 0)

    def recent_summaries(self, agent_id: str, limit: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_summaries WHERE agent_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def upsert_person_memory(
        self, observer_id: str, subject_id: str, impression: str
    ) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO person_memories "
                "(observer_id, subject_id, impression, last_updated) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(observer_id, subject_id) DO UPDATE SET "
                "impression = excluded.impression, "
                "last_updated = excluded.last_updated",
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
                "WHERE observer_id = ? ORDER BY last_updated DESC",
                (observer_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def agent_decision_history(
        self, agent_id: str, limit: int = 20
    ) -> list[dict]:
        """Reconstruct an agent's recent applied decisions.

        Sources merged in time order:
          - conversations where initiator_id = agent (start_1to1 / start_group)
          - public_events of type 'broadcast_agent' from this agent
          - public_events of type 'vote_proposed' by this agent
        do_nothing and join_group are not represented (they leave no
        per-agent trace), but the three above cover the decisions a user
        actually wants to inspect.
        """
        out: list[dict] = []
        with self._lock:
            convs = self._conn.execute(
                "SELECT id, type, participants, started_at, summary "
                "FROM conversations WHERE initiator_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
            for r in convs:
                d = dict(r)
                try:
                    d["participants"] = json.loads(d["participants"])
                except (TypeError, json.JSONDecodeError):
                    d["participants"] = []
                out.append({
                    "kind": "started_conversation",
                    "at": d["started_at"],
                    "conv_id": d["id"],
                    "conv_type": d["type"],
                    "participants": d["participants"],
                    "summary": d.get("summary"),
                })
            evs = self._conn.execute(
                "SELECT event_type, payload, ts FROM public_events "
                "WHERE event_type IN ('broadcast_agent', 'vote_proposed') "
                "ORDER BY id DESC LIMIT ?",
                (limit * 4,),
            ).fetchall()
            for r in evs:
                try:
                    payload = json.loads(r["payload"]) if r["payload"] else {}
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                if r["event_type"] == "broadcast_agent":
                    if payload.get("sender_id") != agent_id:
                        continue
                    out.append({
                        "kind": "broadcast",
                        "at": r["ts"],
                        "message": payload.get("message"),
                    })
                elif r["event_type"] == "vote_proposed":
                    if payload.get("proposer_id") != agent_id:
                        continue
                    out.append({
                        "kind": "vote_proposed",
                        "at": r["ts"],
                        "target_id": payload.get("target_id"),
                        "change_type": payload.get("change_type"),
                        "from_value": payload.get("from_value"),
                        "to_value": payload.get("to_value"),
                    })
        out.sort(key=lambda d: d.get("at") or "", reverse=True)
        return out[:limit]

    # ----- events -----

    def record_event(self, event_type: str, payload: dict) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO public_events (event_type, payload, ts) "
                "VALUES (?, ?, ?)",
                (event_type, json.dumps(payload), utc_now_iso()),
            )
            return int(cur.lastrowid or 0)

    def recent_events(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM public_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            out = []
            for r in reversed(rows):
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

    # ----- analytics helpers (used by the graph renderer) -----

    def all_messages_with_conv(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id, m.conversation_id, m.sender_id, m.ts, "
                "       c.participants, c.type "
                "FROM messages m JOIN conversations c "
                "  ON m.conversation_id = c.id "
                "ORDER BY m.id"
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["participants"] = json.loads(d["participants"])
                except (TypeError, json.JSONDecodeError):
                    d["participants"] = []
                out.append(d)
            return out
