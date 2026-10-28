CREATE TABLE facts (
    pk                INTEGER PRIMARY KEY,
    id                TEXT NOT NULL UNIQUE,
    user_id           TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    text              TEXT NOT NULL,
    category          TEXT NOT NULL DEFAULT 'other'
                      CHECK (category IN ('personal', 'relationship', 'work', 'health',
                                          'preference', 'event', 'other')),
    importance        INTEGER NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
    source_session_id TEXT REFERENCES sessions (id) ON DELETE SET NULL,
    superseded_by     TEXT,
    created_at        TEXT NOT NULL,
    retired_at        TEXT
);

CREATE INDEX facts_user_active ON facts (user_id, retired_at, importance DESC, created_at DESC);

CREATE VIRTUAL TABLE facts_fts USING fts5 (
    text,
    content = 'facts',
    content_rowid = 'pk',
    tokenize = 'unicode61 remove_diacritics 0'
);

CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts (rowid, text) VALUES (new.pk, new.text);
END;

CREATE TRIGGER facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts (facts_fts, rowid, text) VALUES ('delete', old.pk, old.text);
END;

CREATE TRIGGER facts_au AFTER UPDATE OF text ON facts BEGIN
    INSERT INTO facts_fts (facts_fts, rowid, text) VALUES ('delete', old.pk, old.text);
    INSERT INTO facts_fts (rowid, text) VALUES (new.pk, new.text);
END;
