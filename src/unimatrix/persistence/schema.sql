-- Per-run schema. The global registry lives in a separate db; see registry.py.

CREATE TABLE IF NOT EXISTS agents (
    agent_id      TEXT PRIMARY KEY,
    name          TEXT,
    gender        TEXT,
    role          TEXT,
    class         TEXT,
    personality   TEXT,
    values_json   TEXT,
    backstory     TEXT,
    social_need   REAL,
    state         TEXT,
    current_conversation_id INTEGER
);

CREATE TABLE IF NOT EXISTS conversations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    type          TEXT,
    initiator_id  TEXT,
    participants  TEXT,
    started_at    TEXT,
    ended_at      TEXT,
    end_reason    TEXT,
    summary       TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    turn_index      INTEGER,
    sender_id       TEXT,
    content         TEXT,
    ts              TEXT,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS vote_proposals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    proposer_id   TEXT,
    target_id     TEXT,
    change_type   TEXT,
    from_value    TEXT,
    to_value      TEXT,
    proposed_at   TEXT,
    closed_at     TEXT,
    outcome       TEXT,
    yes_count     INTEGER,
    no_count      INTEGER
);

CREATE TABLE IF NOT EXISTS votes (
    proposal_id   INTEGER,
    voter_id      TEXT,
    vote          TEXT,
    reasoning     TEXT,
    voted_at      TEXT,
    PRIMARY KEY (proposal_id, voter_id),
    FOREIGN KEY (proposal_id) REFERENCES vote_proposals(id)
);

CREATE TABLE IF NOT EXISTS status_changes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id      TEXT,
    change_type   TEXT,
    from_value    TEXT,
    to_value      TEXT,
    proposal_id   INTEGER,
    ts            TEXT
);

CREATE TABLE IF NOT EXISTS memory_summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        TEXT,
    conversation_id INTEGER,
    summary         TEXT,
    ts              TEXT
);

CREATE TABLE IF NOT EXISTS person_memories (
    observer_id   TEXT,
    subject_id    TEXT,
    impression    TEXT,
    last_updated  TEXT,
    PRIMARY KEY (observer_id, subject_id)
);

CREATE TABLE IF NOT EXISTS public_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT,
    payload    TEXT,
    ts         TEXT
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    TEXT,
    state TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_conv     ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_events_ts         ON public_events(ts);
CREATE INDEX IF NOT EXISTS idx_summaries_agent   ON memory_summaries(agent_id);
CREATE INDEX IF NOT EXISTS idx_status_agent      ON status_changes(agent_id);
