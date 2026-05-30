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
        if "destitute" not in agent_cols:
            self._conn.execute(
                "ALTER TABLE agents ADD COLUMN destitute INTEGER DEFAULT 0"
            )
        if "prestige" not in agent_cols:
            self._conn.execute(
                "ALTER TABLE agents ADD COLUMN prestige REAL DEFAULT 0"
            )
        if "popularity" not in agent_cols:
            self._conn.execute(
                "ALTER TABLE agents ADD COLUMN popularity REAL DEFAULT 0"
            )
        if "office" not in agent_cols:
            self._conn.execute(
                "ALTER TABLE agents ADD COLUMN office TEXT"
            )
        msg_cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "tick_no" not in msg_cols:
            self._conn.execute("ALTER TABLE messages ADD COLUMN tick_no INTEGER")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_tick ON messages(tick_no)"
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
                   bank_account, destitute, prestige, popularity, office)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    bank_account = excluded.bank_account,
                    destitute = excluded.destitute,
                    prestige = excluded.prestige,
                    popularity = excluded.popularity,
                    office = excluded.office
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
                    int(bool(agent.get("destitute", False))),
                    agent.get("prestige", 0.0),
                    agent.get("popularity", 0.0),
                    agent.get("office"),
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
        destitute: bool | None = None,
        prestige: float | None = None,
        popularity: float | None = None,
        office: str | None | object = ...,
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
        if destitute is not None:
            sets.append("destitute = ?")
            params.append(int(bool(destitute)))
        if prestige is not None:
            sets.append("prestige = ?")
            params.append(prestige)
        if popularity is not None:
            sets.append("popularity = ?")
            params.append(popularity)
        if office is not ...:
            sets.append("office = ?")
            params.append(office)
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

    # ----- loans -----

    def create_loan(
        self,
        borrower_id: str,
        granted_by: str | None,
        principal: float,
        interest_rate: float,
        installment_amount: float,
        installments_total: int,
    ) -> int:
        """Record a new active loan and return its id.

        The principal transfer itself is recorded separately via
        `apply_transaction('loan', ...)`; this row tracks the amortization
        schedule so the economy module can debit installments per tick.
        """
        with self._tx() as c:
            cur = c.execute(
                """
                INSERT INTO loans
                  (borrower_id, granted_by, principal, interest_rate,
                   installment_amount, installments_total, installments_paid,
                   granted_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'active')
                """,
                (
                    borrower_id,
                    granted_by,
                    float(principal),
                    float(interest_rate),
                    float(installment_amount),
                    int(installments_total),
                    utc_now_iso(),
                ),
            )
            return int(cur.lastrowid or 0)

    def list_active_loans(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM loans WHERE status = 'active' ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def record_loan_installment(self, loan_id: int) -> None:
        """Bump installments_paid; if it now equals installments_total, close
        the loan as 'repaid'."""
        with self._tx() as c:
            c.execute(
                "UPDATE loans SET installments_paid = installments_paid + 1 "
                "WHERE id = ?",
                (loan_id,),
            )
            c.execute(
                "UPDATE loans SET status = 'repaid', closed_at = ? "
                "WHERE id = ? AND installments_paid >= installments_total",
                (utc_now_iso(), loan_id),
            )

    def write_off_loan(self, loan_id: int) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE loans SET status = 'written_off', closed_at = ? "
                "WHERE id = ?",
                (utc_now_iso(), loan_id),
            )

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

    # ----- messages (tick-based async messaging) -----

    @staticmethod
    def _msg_row(r: sqlite3.Row) -> dict:
        """Convert a message row whose recipients were group_concat'd into a
        dict with a `recipients` list."""
        d = dict(r)
        rec = d.pop("recipients", None)
        d["recipients"] = rec.split(",") if rec else []
        return d

    def add_message(
        self,
        sender_id: str,
        recipient_ids: Iterable[str],
        content: str,
        tick_no: int,
    ) -> int:
        """Persist one message + its recipient rows in a single transaction.

        `conversation_id`/`turn_index` are left NULL (legacy columns); the
        message is keyed by `tick_no` for the async model.
        """
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
                    "(message_id, recipient_id) VALUES (?, ?)",
                    (mid, rid),
                )
            return mid

    def messages_in_tick(self, tick_no: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id, m.sender_id, m.content, m.ts, m.tick_no, "
                "       group_concat(r.recipient_id) AS recipients "
                "FROM messages m LEFT JOIN message_recipients r "
                "  ON r.message_id = m.id "
                "WHERE m.tick_no = ? GROUP BY m.id ORDER BY m.id",
                (tick_no,),
            ).fetchall()
            return [self._msg_row(r) for r in rows]

    def unread_messages_for(self, agent_id: str, after_tick: int) -> list[dict]:
        """Messages addressed to `agent_id` sent strictly after `after_tick`,
        each decorated with its full recipient list. Used to re-derive an
        inbox on resume."""
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
        """Latest messages where the agent is sender OR recipient, oldest
        first (for prompt history + reflection)."""
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

    def messages_by_sender(self, sender_id: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id, m.sender_id, m.content, m.ts, m.tick_no, "
                "       group_concat(r.recipient_id) AS recipients "
                "FROM messages m LEFT JOIN message_recipients r "
                "  ON r.message_id = m.id "
                "WHERE m.sender_id = ? GROUP BY m.id ORDER BY m.id DESC LIMIT ?",
                (sender_id, limit),
            ).fetchall()
            return [self._msg_row(r) for r in rows]

    def list_messages(
        self,
        limit: int = 500,
        sender: str | None = None,
        recipient: str | None = None,
        text: str | None = None,
    ) -> list[dict]:
        """Explorer feed: newest-first messages from the async stream
        (tick_no set), with optional sender/recipient/text filters."""
        clauses = ["m.tick_no IS NOT NULL"]
        params: list[Any] = []
        if sender:
            clauses.append("m.sender_id = ?")
            params.append(sender)
        if recipient:
            clauses.append(
                "m.id IN (SELECT message_id FROM message_recipients "
                "WHERE recipient_id = ?)"
            )
            params.append(recipient)
        if text:
            clauses.append("m.content LIKE ?")
            params.append(f"%{text}%")
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id, m.sender_id, m.content, m.ts, m.tick_no, "
                "       group_concat(r.recipient_id) AS recipients "
                "FROM messages m LEFT JOIN message_recipients r "
                "  ON r.message_id = m.id "
                "WHERE " + " AND ".join(clauses) +
                " GROUP BY m.id ORDER BY m.id DESC LIMIT ?",
                params,
            ).fetchall()
            return [self._msg_row(r) for r in rows]

    def messages_between(
        self, a_id: str, b_id: str, limit: int = 500
    ) -> list[dict]:
        """Pairwise thread: messages a→b or b→a, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id, m.sender_id, m.content, m.ts, m.tick_no, "
                "       group_concat(r.recipient_id) AS recipients "
                "FROM messages m LEFT JOIN message_recipients r "
                "  ON r.message_id = m.id "
                "WHERE (m.sender_id = ? AND m.id IN "
                "        (SELECT message_id FROM message_recipients "
                "         WHERE recipient_id = ?)) "
                "   OR (m.sender_id = ? AND m.id IN "
                "        (SELECT message_id FROM message_recipients "
                "         WHERE recipient_id = ?)) "
                "GROUP BY m.id ORDER BY m.id ASC LIMIT ?",
                (a_id, b_id, b_id, a_id, limit),
            ).fetchall()
            return [self._msg_row(r) for r in rows]

    def all_messages_with_recipients(self) -> list[dict]:
        """Every async message with its recipient list (graph renderer).
        Inner join excludes legacy conversation rows lacking recipients."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id, m.sender_id, m.ts, m.tick_no, "
                "       group_concat(r.recipient_id) AS recipients "
                "FROM messages m JOIN message_recipients r "
                "  ON r.message_id = m.id "
                "GROUP BY m.id ORDER BY m.id"
            ).fetchall()
            return [self._msg_row(r) for r in rows]

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
          - messages sent by this agent (sent_message)
          - public_events of type 'broadcast_agent' from this agent
          - public_events of type 'vote_proposed' by this agent
        do_nothing leaves no per-agent trace, but the three above cover the
        decisions a user actually wants to inspect.
        """
        out: list[dict] = []
        for m in self.messages_by_sender(agent_id, limit):
            out.append({
                "kind": "sent_message",
                "at": m.get("ts"),
                "message_id": m.get("id"),
                "tick_no": m.get("tick_no"),
                "recipients": m.get("recipients", []),
                "content": (m.get("content") or "")[:200],
            })
        with self._lock:
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

    def latest_checkpoint(self) -> dict | None:
        """Return the most recently written checkpoint payload, or None."""
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
