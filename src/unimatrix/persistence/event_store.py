"""One SQLite database per episode, durable decisions and atomic barriers."""

import json
import sqlite3
from pathlib import Path

from ..core.ids import canonical, digest
from ..core.state import WorldState

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
PRAGMA user_version=4;
PRAGMA application_id=1431132164;
CREATE TABLE IF NOT EXISTS run(id INTEGER PRIMARY KEY CHECK(id=1), manifest TEXT NOT NULL, status TEXT NOT NULL, completed_tick INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, tick INTEGER NOT NULL, body TEXT NOT NULL, event_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(tick INTEGER PRIMARY KEY, state TEXT NOT NULL, state_hash TEXT NOT NULL, last_seq INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(tick INTEGER, slot TEXT, request_id TEXT UNIQUE, observation TEXT NOT NULL, raw TEXT NOT NULL, PRIMARY KEY(tick,slot));
CREATE TABLE IF NOT EXISTS model_calls(request_id TEXT, attempt INTEGER, body TEXT NOT NULL, PRIMARY KEY(request_id,attempt));
CREATE TRIGGER IF NOT EXISTS no_decision_update BEFORE UPDATE ON decisions BEGIN SELECT RAISE(ABORT,'immutable decision'); END;
CREATE TRIGGER IF NOT EXISTS no_decision_delete BEFORE DELETE ON decisions BEGIN SELECT RAISE(ABORT,'immutable decision'); END;
CREATE TRIGGER IF NOT EXISTS no_event_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS no_event_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append only'); END;
"""


class EventStore:
    def __init__(self, path, read_only=False):
        self.path = Path(path)
        if read_only:
            if not self.path.is_file():
                raise ValueError("benchmark_database_missing")
            self.db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
            if self.db.execute("PRAGMA application_id").fetchone()[0] != 1431132164:
                self.db.close()
                raise ValueError("not_a_benchmark_database")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path)
            if self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                if self.db.execute("PRAGMA application_id").fetchone()[0] != 1431132164:
                    self.db.close()
                    raise ValueError("incompatible_database")
            self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    @property
    def manifest(self):
        row = self.db.execute("SELECT manifest FROM run").fetchone()
        return json.loads(row[0]) if row else None

    def initialize(self, manifest, state):
        if self.manifest is not None:
            if self.manifest != manifest:
                raise ValueError("immutable_manifest_mismatch")
            return
        with self.db:
            self.db.execute("INSERT INTO run VALUES(1,?,?,0)", (canonical(manifest), "paused"))
            self._append(
                0,
                [
                    dict(
                        type="world_initialized",
                        phase="initialization",
                        actor_slot=None,
                        payload=dict(state=state.dump(), manifest_hash=digest(manifest)),
                        visibility=["evaluator"],
                    )
                ],
            )
            self._snapshot(state)

    def status(self):
        row = self.db.execute("SELECT status,completed_tick FROM run").fetchone()
        return dict(status=row[0], completed_tick=row[1])

    def set_status(self, status):
        if status not in {
            "running",
            "paused",
            "aborted",
            "infra_failed",
            "completed",
            "engine_failed",
        }:
            raise ValueError("unknown_status")
        if status == "completed" and self.status()["completed_tick"] != self.manifest["ticks"]:
            raise ValueError("incomplete_episode")
        with self.db:
            self.db.execute("UPDATE run SET status=?", (status,))

    def _append(self, tick, events):
        row = self.db.execute(
            "SELECT seq,event_hash FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        seq, previous = row if row else (0, "0" * 64)
        for item in events:
            seq += 1
            body = dict(
                item,
                run_id=self.manifest["run_id"],
                seq=seq,
                tick=tick,
                previous_hash=previous,
                cause_ids=item.get("cause_ids", []),
            )
            body["actor_id"] = body.pop("actor_slot")
            readers = body["visibility"]
            body["visibility"] = dict(
                scope="public"
                if "public" in readers
                else "evaluator"
                if "evaluator" in readers
                else "private",
                reader_ids=[]
                if "public" in readers or "evaluator" in readers
                else list(dict.fromkeys(readers)),
            )
            previous = digest(body)
            self.db.execute(
                "INSERT INTO events VALUES(?,?,?,?)", (seq, tick, canonical(body), previous)
            )

    def _snapshot(self, state, data=None):
        data = state.dump() if data is None else data
        seq = self.db.execute("SELECT MAX(seq) FROM events").fetchone()[0]
        self.db.execute(
            "INSERT INTO snapshots VALUES(?,?,?,?)",
            (state.tick, canonical(data), digest(data), seq),
        )

    def commit(self, before, after, events):
        if after.tick != before.tick + 1 or after.tick > self.manifest["ticks"]:
            raise ValueError("invalid_tick_transition")
        selected = []
        requests = {}
        for slot, request, observation, raw in self.db.execute(
            "SELECT slot,request_id,observation,raw FROM decisions WHERE tick=? ORDER BY slot",
            (before.tick,),
        ):
            requests[slot] = request
            selected.append(
                dict(
                    type="decision_recorded",
                    phase="validate",
                    actor_slot=slot,
                    visibility=[slot],
                    payload=dict(
                        request_id=request,
                        observation_hash=digest(json.loads(observation)),
                        raw_hash=digest(raw),
                    ),
                )
            )
        events = selected + [
            dict(
                item,
                cause_ids=[requests[item["actor_slot"]]]
                if item.get("actor_slot") in requests
                else item.get("cause_ids", []),
            )
            for item in events
        ]
        # The final event is a canonical replacement projection, making replay
        # independent of subsequent resolver versions.
        after_data = after.dump()
        events = events + [
            dict(
                type="state_committed",
                phase="commit",
                actor_slot=None,
                visibility=["evaluator"],
                payload=dict(before_hash=digest(before.dump()), state=after_data),
            )
        ]
        with self.db:
            cursor = self.db.execute(
                "UPDATE run SET completed_tick=?,status=? WHERE completed_tick=?",
                (
                    after.tick,
                    "completed" if after.tick == self.manifest["ticks"] else "running",
                    before.tick,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("stale_barrier")
            self._append(after.tick, events)
            self._snapshot(after, after_data)

    def load(self):
        row = self.db.execute(
            "SELECT state,state_hash FROM snapshots ORDER BY tick DESC LIMIT 1"
        ).fetchone()
        state = json.loads(row[0])
        if digest(state) != row[1]:
            raise ValueError("snapshot_hash_mismatch")
        return WorldState.load(state)

    def events(self):
        for raw, checksum in self.db.execute("SELECT body,event_hash FROM events ORDER BY seq"):
            yield dict(json.loads(raw), event_hash=checksum)

    def verify(self):
        previous, state, seq = "0" * 64, None, 0
        for item in self.events():
            checksum = item.pop("event_hash")
            seq += 1
            if item["seq"] != seq or item["previous_hash"] != previous or digest(item) != checksum:
                raise ValueError("event_chain_mismatch")
            if item["run_id"] != self.manifest["run_id"]:
                raise ValueError("event_run_mismatch")
            previous = checksum
            if item["type"] == "decision_recorded":
                record = self.db.execute(
                    "SELECT observation,raw FROM decisions WHERE request_id=?",
                    (item["payload"]["request_id"],),
                ).fetchone()
                if (
                    not record
                    or digest(json.loads(record[0])) != item["payload"]["observation_hash"]
                    or digest(record[1]) != item["payload"]["raw_hash"]
                ):
                    raise ValueError("decision_evidence_mismatch")
            if item["type"] == "world_initialized":
                if item["payload"]["manifest_hash"] != digest(self.manifest):
                    raise ValueError("manifest_hash_mismatch")
                state = item["payload"]["state"]
            elif item["type"] == "state_committed":
                if digest(state) != item["payload"]["before_hash"]:
                    raise ValueError("state_chain_mismatch")
                state = item["payload"]["state"]
            if item["type"] in {"world_initialized", "state_committed"}:
                snapshot = self.db.execute(
                    "SELECT state_hash,last_seq FROM snapshots WHERE tick=?", (state["tick"],)
                ).fetchone()
                if snapshot != (digest(state), seq):
                    raise ValueError("snapshot_event_mismatch")
        if state != self.load().dump() or state["tick"] != self.status()["completed_tick"]:
            raise ValueError("projection_mismatch")
        return dict(
            state_hash=digest(state), event_hash=previous, completed_tick=state["tick"], events=seq
        )

    def decision(self, tick, slot, request_id):
        row = self.db.execute(
            "SELECT request_id,raw FROM decisions WHERE tick=? AND slot=?", (tick, slot)
        ).fetchone()
        if row and row[0] != request_id:
            raise ValueError("observation_or_policy_changed")
        return row[1] if row else None

    def save_decision(self, tick, slot, request_id, observation, raw):
        with self.db:
            self.db.execute(
                "INSERT INTO decisions VALUES(?,?,?,?,?)",
                (tick, slot, request_id, canonical(observation), raw),
            )

    def record_call(self, request_id, attempt, data):
        with self.db:
            self.db.execute(
                "INSERT INTO model_calls VALUES(?,?,?)", (request_id, attempt, canonical(data))
            )

    def attempts(self, request_id):
        return self.db.execute(
            "SELECT COUNT(*) FROM model_calls WHERE request_id=?", (request_id,)
        ).fetchone()[0]
