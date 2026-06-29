-- Per-run schema. The global run index lives in a separate db; see registry.py.

-- The beings: self-authored identity + finitude. No imposed role/class/economy.
CREATE TABLE IF NOT EXISTS agents (
    agent_id            TEXT PRIMARY KEY,
    name                TEXT,
    gender              TEXT,
    circumstance        TEXT,       -- thin seed: the situation they woke into
    disposition         TEXT,       -- thin seed: one provisional leaning
    self_model_json     TEXT,       -- the current self-authored identity
    self_model_version  INTEGER DEFAULT 0,
    vitality            REAL DEFAULT 100,
    alive               INTEGER DEFAULT 1,
    sustenance          REAL DEFAULT 0,
    born_tick           INTEGER DEFAULT 0,  -- 0 for founders, >0 for successors
    parent_ids          TEXT,               -- JSON list, for successors
    traits_json         TEXT,               -- heritable biology (JSON), mutated at birth
    pos_x               INTEGER DEFAULT 0,  -- position on the patch grid (ecology mode)
    pos_y               INTEGER DEFAULT 0,
    social_need         REAL DEFAULT 100,
    state               TEXT DEFAULT 'idle' -- 'idle' | 'dead'
);

-- Versioned history of each being's identity. One row per self-revision; the
-- diff is the observable record of a being becoming itself.
CREATE TABLE IF NOT EXISTS self_models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        TEXT NOT NULL,
    version         INTEGER NOT NULL,
    tick_no         INTEGER,
    self_model_json TEXT,
    diff            TEXT,
    ts              TEXT
);
CREATE INDEX IF NOT EXISTS idx_self_models_agent ON self_models(agent_id);

-- Words exchanged between beings.
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id   TEXT,
    content     TEXT,
    ts          TEXT,
    tick_no     INTEGER
);
CREATE TABLE IF NOT EXISTS message_recipients (
    message_id   INTEGER NOT NULL,
    recipient_id TEXT    NOT NULL,
    PRIMARY KEY (message_id, recipient_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_tick     ON messages(tick_no);
CREATE INDEX IF NOT EXISTS idx_msgrcpt_recipient ON message_recipients(recipient_id);

-- Memory: periodic first-person reflections + deed traces.
CREATE TABLE IF NOT EXISTS memory_summaries (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id  TEXT,
    summary   TEXT,
    ts        TEXT
);
CREATE INDEX IF NOT EXISTS idx_summaries_agent ON memory_summaries(agent_id);

-- A being's evolving subjective impression of another.
CREATE TABLE IF NOT EXISTS person_memories (
    observer_id   TEXT,
    subject_id    TEXT,
    impression    TEXT,
    last_updated  TEXT,
    PRIMARY KEY (observer_id, subject_id)
);

-- MEANING & BELIEF: a public commons of authored ideas + adoption lineage.
CREATE TABLE IF NOT EXISTS cultural_artifacts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id           TEXT,
    kind                TEXT,        -- 'belief' | 'name' | 'story' | 'norm' | 'rule'
    content             TEXT,
    tick_no             INTEGER,
    parent_artifact_id  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_artifacts_author ON cultural_artifacts(author_id);
CREATE TABLE IF NOT EXISTS belief_adoptions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id            TEXT NOT NULL,
    artifact_id         INTEGER NOT NULL,
    self_model_version  INTEGER,
    tick_no             INTEGER
);
CREATE INDEX IF NOT EXISTS idx_adopt_artifact ON belief_adoptions(artifact_id);

-- KINSHIP & INTIMACY: typed, directed, durable ties.
CREATE TABLE IF NOT EXISTS relationships (
    observer_id    TEXT NOT NULL,
    subject_id     TEXT NOT NULL,
    type           TEXT,     -- 'friend'|'ally'|'rival'|'mentor'|'partner'|'kin'
    strength       REAL DEFAULT 0,
    history        TEXT,
    formed_tick    INTEGER,
    updated_tick   INTEGER,
    dissolved_tick INTEGER,
    PRIMARY KEY (observer_id, subject_id)
);
CREATE INDEX IF NOT EXISTS idx_rel_subject ON relationships(subject_id);

-- MORTALITY & CONTINUITY: lineage of successors + memorials of the dead.
CREATE TABLE IF NOT EXISTS lineage (
    child_id       TEXT PRIMARY KEY,
    parent_ids     TEXT,
    inherited_json TEXT,
    tick_no        INTEGER
);
CREATE TABLE IF NOT EXISTS deaths (
    agent_id        TEXT PRIMARY KEY,
    cause           TEXT,
    tick_no         INTEGER,
    legacy_summary  TEXT,
    ts              TEXT
);

-- PLACE: the patch grid (ecology mode). Resources deplete when harvested and
-- regrow slowly when left alone, so ground gets used up and richer ground lies
-- elsewhere — scarcity becomes local.
CREATE TABLE IF NOT EXISTS patches (
    x            INTEGER NOT NULL,
    y            INTEGER NOT NULL,
    resource     REAL DEFAULT 0,
    capacity     REAL DEFAULT 0,
    regen        REAL DEFAULT 0,
    updated_tick INTEGER,
    PRIMARY KEY (x, y)
);

-- Public event log (everything that happens) + full-state checkpoints.
CREATE TABLE IF NOT EXISTS public_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT,
    payload    TEXT,
    ts         TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON public_events(ts);
CREATE TABLE IF NOT EXISTS checkpoints (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    TEXT,
    state TEXT
);
