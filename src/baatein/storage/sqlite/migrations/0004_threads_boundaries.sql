CREATE TABLE threads (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    kind              TEXT NOT NULL DEFAULT 'plan'
                      CHECK (kind IN ('relationship', 'aspiration', 'intention', 'worry', 'plan',
                                      'story')),
    title             TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'in_progress', 'done', 'not_done', 'cancelled',
                                        'snoozed')),
    next_step         TEXT,
    check_in_after    TEXT,
    last_offered_at   TEXT,
    times_offered     INTEGER NOT NULL DEFAULT 0,
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal', 'personal', 'health', 'grief', 'crisis')),
    about_person_id   TEXT REFERENCES people (id) ON DELETE SET NULL,
    source_session_id TEXT REFERENCES sessions (id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL
);

CREATE INDEX threads_user_status ON threads (user_id, status);

CREATE TABLE thread_events (
    id         TEXT PRIMARY KEY,
    thread_id  TEXT NOT NULL REFERENCES threads (id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    status     TEXT CHECK (status IN ('open', 'in_progress', 'done', 'not_done', 'cancelled',
                                      'snoozed')),
    session_id TEXT REFERENCES sessions (id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX thread_events_thread ON thread_events (thread_id, created_at);

CREATE TABLE boundaries (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    topic      TEXT NOT NULL,
    scope      TEXT NOT NULL DEFAULT 'until_reopened'
               CHECK (scope IN ('this_call', 'until_reopened', 'until_date')),
    session_id TEXT REFERENCES sessions (id) ON DELETE SET NULL,
    until      TEXT,
    thread_id  TEXT REFERENCES threads (id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    lifted_at  TEXT
);

CREATE INDEX boundaries_user ON boundaries (user_id, lifted_at);

CREATE TABLE declined_suggestions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    session_id TEXT REFERENCES sessions (id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX declined_suggestions_user ON declined_suggestions (user_id, created_at);

ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata';

ALTER TABLE sessions ADD COLUMN ended_abruptly INTEGER NOT NULL DEFAULT 0;

ALTER TABLE sessions ADD COLUMN unfinished_topic TEXT;

INSERT INTO threads (id, user_id, title, status, check_in_after, source_session_id, created_at)
SELECT id, user_id, text,
       CASE status WHEN 'open' THEN 'open' WHEN 'done' THEN 'done' ELSE 'cancelled' END,
       due_at, source_session_id, created_at
FROM followups;

DROP TABLE followups;
