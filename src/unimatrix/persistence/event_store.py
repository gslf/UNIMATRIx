"""One SQLite database per episode, durable decisions and atomic barriers."""

import json
import sqlite3
import zlib
from pathlib import Path

from ..core.ids import canonical, digest, digest_text
from ..core.state import WorldState

APPLICATION_ID = 1431132164
VERSION = 6
STATE_EVENTS = ("world_initialized", "state_committed")

SCHEMA = """
PRAGMA page_size=16384;
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
PRAGMA user_version=6;
PRAGMA application_id=1431132164;
CREATE TABLE IF NOT EXISTS run(id INTEGER PRIMARY KEY CHECK(id=1), manifest TEXT NOT NULL, status TEXT NOT NULL, completed_tick INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, tick INTEGER NOT NULL, type TEXT NOT NULL, body TEXT NOT NULL, event_hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_type ON events(type, seq);
CREATE TABLE IF NOT EXISTS snapshots(tick INTEGER PRIMARY KEY, state TEXT NOT NULL, state_hash TEXT NOT NULL, last_seq INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS memories(seq INTEGER PRIMARY KEY, tick INTEGER NOT NULL, slot TEXT NOT NULL, generation INTEGER NOT NULL, entries BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS memories_slot ON memories(slot, seq);
CREATE TABLE IF NOT EXISTS decisions(tick INTEGER, slot TEXT, request_id TEXT UNIQUE, observation BLOB NOT NULL, observation_hash TEXT NOT NULL, raw TEXT NOT NULL, raw_hash TEXT NOT NULL, PRIMARY KEY(tick,slot));
CREATE TABLE IF NOT EXISTS model_calls(request_id TEXT, attempt INTEGER, body TEXT NOT NULL, PRIMARY KEY(request_id,attempt));
CREATE TABLE IF NOT EXISTS blobs(name TEXT PRIMARY KEY, data BLOB NOT NULL);
CREATE TRIGGER IF NOT EXISTS no_decision_update BEFORE UPDATE ON decisions BEGIN SELECT RAISE(ABORT,'immutable decision'); END;
CREATE TRIGGER IF NOT EXISTS no_decision_delete BEFORE DELETE ON decisions BEGIN SELECT RAISE(ABORT,'immutable decision'); END;
CREATE TRIGGER IF NOT EXISTS no_event_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS no_event_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS no_memory_update BEFORE UPDATE ON memories BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS no_memory_delete BEFORE DELETE ON memories BEGIN SELECT RAISE(ABORT,'append only'); END;
"""


def observation_dictionary():
    """Shared compression dictionary: the protocol text every observation repeats."""
    from ..actions.schemas import INTERFACE
    from ..core.visibility import PROTOCOL
    from ..scenarios import RULE_TEXTS

    return canonical(dict(interface=INTERFACE, protocol=PROTOCOL, rules=RULE_TEXTS)).encode("utf-8")


class EventStore:
    def __init__(self, path, read_only=False):
        self.path = Path(path)
        self._manifest = None
        self._verified = None
        if read_only:
            if not self.path.is_file():
                raise ValueError("benchmark_database_missing")
            self.db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
            self._require_format("not_a_benchmark_database")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path)
            if self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                self._require_format("incompatible_database")
            self.db.executescript(SCHEMA)
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO blobs VALUES('observation_dictionary',?)",
                    (observation_dictionary(),),
                )
        row = self.db.execute("SELECT data FROM blobs WHERE name='observation_dictionary'").fetchone()
        self._zdict = bytes(row[0]) if row else b""

    def _require_format(self, error):
        found = [
            self.db.execute(f"PRAGMA {name}").fetchone()[0]
            for name in ("application_id", "user_version")
        ]
        if found != [APPLICATION_ID, VERSION]:
            self.db.close()
            raise ValueError(error)

    def close(self):
        self.db.close()

    @property
    def manifest(self):
        if self._manifest is None:
            row = self.db.execute("SELECT manifest FROM run").fetchone()
            self._manifest = json.loads(row[0]) if row else None
        return self._manifest

    def initialize(self, manifest, state):
        if self.manifest is not None:
            if self.manifest != manifest:
                raise ValueError("immutable_manifest_mismatch")
            return
        with self.db:
            self.db.execute("INSERT INTO run VALUES(1,?,?,0)", (canonical(manifest), "paused"))
            self._manifest = None
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
        if status == "completed":
            self.db.execute("VACUUM")

    def _append(self, tick, events):
        row = self.db.execute(
            "SELECT seq,event_hash FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        seq, previous = row if row else (0, "0" * 64)
        run_id = self.manifest["run_id"]
        for item in events:
            seq += 1
            body = dict(
                item,
                run_id=run_id,
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
            text = canonical(body)
            previous = digest_text(text)
            self.db.execute(
                "INSERT INTO events(seq,tick,type,body,event_hash) VALUES(?,?,?,?,?)",
                (seq, tick, body["type"], text, previous),
            )

    def _snapshot(self, state, text=None, state_hash=None):
        if text is None:
            text = canonical(state.dump())
            state_hash = digest_text(text)
        seq = self.db.execute("SELECT MAX(seq) FROM events").fetchone()[0]
        self.db.execute(
            "INSERT INTO snapshots VALUES(?,?,?,?)",
            (state.tick, self._pack(text), state_hash, seq),
        )

    @staticmethod
    def _memory_delta(before, after):
        """New experience records this tick; a generation change restarts a slot's log."""
        rows = []
        for slot in sorted(after.agents):
            current = after.memories.get(slot, [])
            generation = after.agents[slot]["generation"]
            previous = before.memories.get(slot, [])
            base = 0
            if before.agents.get(slot, {}).get("generation") == generation:
                base = len(previous)
                if len(current) < base or (base and current[base - 1] is not previous[base - 1]):
                    raise ValueError("memory_log_not_append_only")
            if len(current) > base:
                rows.append((after.tick, slot, generation, canonical(current[base:])))
        return rows

    @staticmethod
    def _memories_hash(rows):
        return digest([[slot, generation, entries] for _, slot, generation, entries in rows])

    def commit(self, before, after, events):
        if after.tick != before.tick + 1 or after.tick > self.manifest["ticks"]:
            raise ValueError("invalid_tick_transition")
        selected = []
        requests = {}
        for slot, request, observation_hash, raw_hash in self.db.execute(
            "SELECT slot,request_id,observation_hash,raw_hash FROM decisions WHERE tick=? "
            "ORDER BY slot",
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
                        request_id=request, observation_hash=observation_hash, raw_hash=raw_hash
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
        row = self.db.execute(
            "SELECT state_hash FROM snapshots WHERE tick=?", (before.tick,)
        ).fetchone()
        if row is None:
            raise ValueError("missing_snapshot")
        after_text = canonical(after.dump())
        after_hash = digest_text(after_text)
        memory_rows = self._memory_delta(before, after)


        events = events + [
            dict(
                type="state_committed",
                phase="commit",
                actor_slot=None,
                visibility=["evaluator"],
                payload=dict(
                    before_hash=row[0],
                    after_hash=after_hash,
                    memories_hash=self._memories_hash(memory_rows),
                ),
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
            self._snapshot(after, after_text, after_hash)
            self.db.executemany(
                "INSERT INTO memories(tick,slot,generation,entries) VALUES(?,?,?,?)",
                [(tick, slot, gen, self._pack(text)) for tick, slot, gen, text in memory_rows],
            )



        normalized = WorldState.load(json.loads(after_text))
        normalized.memories = after.memories
        return normalized

    def load(self):
        row = self.db.execute(
            "SELECT state,state_hash FROM snapshots ORDER BY tick DESC LIMIT 1"
        ).fetchone()
        text = self._unpack(row[0])
        if digest_text(text) != row[1]:
            raise ValueError("snapshot_hash_mismatch")
        world = WorldState.load(json.loads(text))
        world.memories = {slot: [] for slot in world.agents}
        for slot, generation, entries in self.db.execute(
            "SELECT slot,generation,entries FROM memories ORDER BY seq"
        ):
            if generation == world.agents.get(slot, {}).get("generation"):
                world.memories.setdefault(slot, []).extend(json.loads(self._unpack(entries)))
        return world

    def state_at(self, tick):
        """World state at a committed tick, including the experience logs up to it."""
        state = self.snapshot(tick)
        if state is None:
            raise ValueError("no_snapshot_at_tick")
        world = WorldState.load(state)
        world.memories = {slot: [] for slot in world.agents}
        rows = self.db.execute(
            "SELECT slot,generation,entries FROM memories WHERE tick<=? ORDER BY seq", (tick,)
        )
        for slot, generation, entries in rows:
            if generation == world.agents.get(slot, {}).get("generation"):
                world.memories.setdefault(slot, []).extend(json.loads(self._unpack(entries)))
        return world

    def events(self, kinds=None, exclude=()):
        clauses, parameters = [], []
        if kinds is not None:
            clauses.append(f"type IN ({','.join('?' * len(kinds))})")
            parameters.extend(kinds)
        if exclude:
            clauses.append(f"type NOT IN ({','.join('?' * len(exclude))})")
            parameters.extend(exclude)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        for raw, checksum, seq, tick, kind in self.db.execute(
            f"SELECT body,event_hash,seq,tick,type FROM events{where} ORDER BY seq", parameters
        ):
            body = json.loads(raw)
            if (body["seq"], body["tick"], body["type"]) != (seq, tick, kind):
                raise ValueError("event_index_mismatch")
            yield dict(body, event_hash=checksum)

    def counts(self):
        return dict(self.db.execute("SELECT type,COUNT(*) FROM events GROUP BY 1").fetchall())

    def _pack(self, text, zdict=b""):
        compressor = zlib.compressobj(6, zdict=zdict)
        return compressor.compress(text.encode("utf-8")) + compressor.flush()

    @staticmethod
    def _unpack(value, zdict=b""):
        decompressor = zlib.decompressobj(zdict=zdict)
        return (decompressor.decompress(bytes(value)) + decompressor.flush()).decode("utf-8")

    def observation_text(self, value):
        """Canonical JSON of a stored observation."""
        return self._unpack(value, self._zdict)

    def observation(self, value):
        return json.loads(self.observation_text(value))

    def snapshot(self, tick=None):
        """Material state at a tick (latest when omitted), or None."""
        if tick is None:
            row = self.db.execute("SELECT state,state_hash FROM snapshots ORDER BY tick DESC LIMIT 1").fetchone()
        else:
            row = self.db.execute("SELECT state,state_hash FROM snapshots WHERE tick=?", (tick,)).fetchone()
        if row is None:
            return None
        text = self._unpack(row[0])
        if digest_text(text) != row[1]:
            raise ValueError("snapshot_hash_mismatch")
        return json.loads(text)

    def verify(self):
        key = (
            self.db.total_changes,
            self.db.execute("PRAGMA data_version").fetchone()[0],
            self.db.execute("SELECT MAX(seq) FROM events").fetchone()[0],
            self.status()["completed_tick"],
        )
        if self._verified is None or self._verified[0] != key:
            self._verified = (key, self._verify())
        return dict(self._verified[1])

    def _verify_chain(self, item, previous, seq):
        checksum = item.pop("event_hash")
        if item["seq"] != seq or item["previous_hash"] != previous or digest(item) != checksum:
            raise ValueError("event_chain_mismatch")
        if item["run_id"] != self.manifest["run_id"]:
            raise ValueError("event_run_mismatch")
        return checksum

    def _verify_decision(self, item):
        record = self.db.execute(
            "SELECT observation,raw,tick,slot,observation_hash,raw_hash FROM decisions WHERE request_id=?",
            (item["payload"]["request_id"],),
        ).fetchone()
        if (
            not record
            or record[2:4] != (item["tick"] - 1, item["actor_id"])
            or record[4:] != (item["payload"]["observation_hash"], item["payload"]["raw_hash"])
            or digest_text(self.observation_text(record[0])) != item["payload"]["observation_hash"]
            or digest(record[1]) != item["payload"]["raw_hash"]
        ):
            raise ValueError("decision_evidence_mismatch")

    def _verify(self):
        self._manifest = None
        previous, state_hash, tick, seq = "0" * 64, None, 0, 0
        manifest_hash = digest(self.manifest)
        for item in self.events():
            seq += 1
            previous = self._verify_chain(item, previous, seq)
            if item["type"] == "decision_recorded":
                self._verify_decision(item)
            elif item["type"] == "world_initialized":
                if item["payload"]["manifest_hash"] != manifest_hash:
                    raise ValueError("manifest_hash_mismatch")
                state_hash, tick = digest(item["payload"]["state"]), 0
            elif item["type"] == "state_committed":
                payload = item["payload"]
                if payload["before_hash"] != state_hash:
                    raise ValueError("state_chain_mismatch")
                state_hash, tick = payload["after_hash"], item["tick"]
                rows = [
                    (t, slot, gen, self._unpack(entries))
                    for t, slot, gen, entries in self.db.execute(
                        "SELECT tick,slot,generation,entries FROM memories WHERE tick=? "
                        "ORDER BY seq",
                        (tick,),
                    )
                ]
                if self._memories_hash(rows) != payload["memories_hash"]:
                    raise ValueError("memory_evidence_mismatch")
            if item["type"] in STATE_EVENTS:
                snapshot = self.db.execute(
                    "SELECT state_hash,last_seq,state FROM snapshots WHERE tick=?", (tick,)
                ).fetchone()
                if snapshot is None or snapshot[:2] != (state_hash, seq):
                    raise ValueError("snapshot_event_mismatch")
                if digest_text(self._unpack(snapshot[2])) != state_hash:
                    raise ValueError("snapshot_hash_mismatch")
        last = self.db.execute(
            "SELECT tick,state_hash FROM snapshots ORDER BY tick DESC LIMIT 1"
        ).fetchone()
        if last != (tick, state_hash) or tick != self.status()["completed_tick"]:
            raise ValueError("projection_mismatch")
        return dict(state_hash=state_hash, event_hash=previous, completed_tick=tick, events=seq)

    def decision(self, tick, slot, request_id):
        row = self.db.execute(
            "SELECT request_id,raw FROM decisions WHERE tick=? AND slot=?", (tick, slot)
        ).fetchone()
        if row and row[0] != request_id:
            raise ValueError("observation_or_policy_changed")
        return row[1] if row else None

    def save_decision(self, tick, slot, request_id, observation, raw, call=None):
        """Persist a successful decision and, atomically, the attempt that produced it."""
        text = canonical(observation)
        with self.db:
            if call is not None:
                attempt, data = call
                self.db.execute(
                    "INSERT INTO model_calls VALUES(?,?,?)", (request_id, attempt, canonical(data))
                )
            self.db.execute(
                "INSERT INTO decisions(tick,slot,request_id,observation,observation_hash,raw,raw_hash)"
                " VALUES(?,?,?,?,?,?,?)",
                (
                    tick,
                    slot,
                    request_id,
                    self._pack(text, self._zdict),
                    digest_text(text),
                    raw,
                    digest(raw),
                ),
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
