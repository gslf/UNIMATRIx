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
    current_conversation_id INTEGER,
    bank_account  REAL DEFAULT 0,
    destitute     INTEGER DEFAULT 0,
    prestige      REAL DEFAULT 0,
    popularity    REAL DEFAULT 0,
    office        TEXT
);

CREATE TABLE IF NOT EXISTS community_account (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    balance REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT,
    kind        TEXT,
    from_party  TEXT,
    to_party    TEXT,
    amount      REAL,
    reason      TEXT,
    ref_id      INTEGER
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

CREATE TABLE IF NOT EXISTS message_recipients (
    message_id   INTEGER NOT NULL,
    recipient_id TEXT    NOT NULL,
    PRIMARY KEY (message_id, recipient_id),
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS vote_proposals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    proposer_id   TEXT,
    target_id     TEXT,
    change_type   TEXT,
    from_value    TEXT,
    to_value      TEXT,
    motivation    TEXT,
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
    motivation    TEXT,
    raw_response  TEXT,
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

CREATE TABLE IF NOT EXISTS loans (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    borrower_id           TEXT NOT NULL,
    granted_by            TEXT,
    principal             REAL NOT NULL,
    interest_rate         REAL NOT NULL,
    installment_amount    REAL NOT NULL,
    installments_total    INTEGER NOT NULL,
    installments_paid     INTEGER NOT NULL DEFAULT 0,
    granted_at            TEXT NOT NULL,
    closed_at             TEXT,
    -- 'active' | 'repaid' | 'written_off'
    status                TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_messages_conv     ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_msgrcpt_recipient ON message_recipients(recipient_id);
CREATE INDEX IF NOT EXISTS idx_events_ts         ON public_events(ts);
CREATE INDEX IF NOT EXISTS idx_summaries_agent   ON memory_summaries(agent_id);
CREATE INDEX IF NOT EXISTS idx_status_agent      ON status_changes(agent_id);
CREATE INDEX IF NOT EXISTS idx_transactions_ts   ON transactions(ts);
CREATE INDEX IF NOT EXISTS idx_transactions_to   ON transactions(to_party);
CREATE INDEX IF NOT EXISTS idx_transactions_from ON transactions(from_party);
CREATE INDEX IF NOT EXISTS idx_loans_borrower    ON loans(borrower_id);
CREATE INDEX IF NOT EXISTS idx_loans_status      ON loans(status);
