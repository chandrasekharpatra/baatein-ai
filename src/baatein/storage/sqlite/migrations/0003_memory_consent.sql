CREATE TABLE people (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    relation   TEXT,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX people_user_name ON people (user_id, name COLLATE NOCASE);

ALTER TABLE facts ADD COLUMN kind TEXT NOT NULL DEFAULT 'fact'
    CHECK (kind IN ('fact', 'story', 'preference', 'moment'));

ALTER TABLE facts ADD COLUMN quote TEXT;

ALTER TABLE facts ADD COLUMN about_person_id TEXT REFERENCES people (id) ON DELETE SET NULL;

ALTER TABLE facts ADD COLUMN certainty TEXT NOT NULL DEFAULT 'sure'
    CHECK (certainty IN ('sure', 'uncertain'));

ALTER TABLE facts ADD COLUMN sensitivity TEXT NOT NULL DEFAULT 'normal'
    CHECK (sensitivity IN ('normal', 'personal', 'health', 'grief', 'crisis'));

ALTER TABLE facts ADD COLUMN deleted_at TEXT;
