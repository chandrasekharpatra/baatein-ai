CREATE TABLE users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    language      TEXT NOT NULL DEFAULT 'hinglish',
    created_at    TEXT NOT NULL
);

CREATE TABLE sessions (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    room_name         TEXT,
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    end_reason        TEXT,
    summary           TEXT,
    finalize_status   TEXT NOT NULL DEFAULT 'active'
                      CHECK (finalize_status IN ('active', 'pending', 'done', 'failed')),
    finalize_attempts INTEGER NOT NULL DEFAULT 0,
    memory_synced_seq INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX sessions_user_started ON sessions (user_id, started_at DESC);
CREATE INDEX sessions_finalize ON sessions (finalize_status, ended_at);

CREATE TABLE turns (
    id          INTEGER PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text        TEXT NOT NULL,
    language    TEXT,
    interrupted INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    UNIQUE (session_id, seq)
);

CREATE VIRTUAL TABLE turns_fts USING fts5 (
    text,
    content = 'turns',
    content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 0'
);

CREATE TRIGGER turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts (rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts (turns_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER turns_au AFTER UPDATE OF text ON turns BEGIN
    INSERT INTO turns_fts (turns_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO turns_fts (rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE followups (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    text              TEXT NOT NULL,
    due_at            TEXT,
    status            TEXT NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'done', 'dropped')),
    priority          INTEGER NOT NULL DEFAULT 0,
    source_session_id TEXT REFERENCES sessions (id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL,
    resolved_at       TEXT
);

CREATE INDEX followups_user_open ON followups (user_id, status, due_at);
