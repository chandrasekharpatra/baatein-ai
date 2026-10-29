CREATE TABLE reminders (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    event_date        TEXT,
    local_time        TEXT NOT NULL DEFAULT '09:00',
    repeat            TEXT NOT NULL DEFAULT 'once'
                      CHECK (repeat IN ('once', 'weekly', 'monthly_date', 'monthly_weekday',
                                        'yearly')),
    alert_days_before TEXT NOT NULL DEFAULT '[0]',
    status            TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('needs_info', 'active', 'done', 'cancelled')),
    note              TEXT,
    about_person_id   TEXT REFERENCES people (id) ON DELETE SET NULL,
    source_session_id TEXT REFERENCES sessions (id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL
);

CREATE INDEX reminders_user_status ON reminders (user_id, status);

CREATE TABLE reminder_occurrences (
    reminder_id TEXT NOT NULL REFERENCES reminders (id) ON DELETE CASCADE,
    occurs_on   TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('done', 'skipped')),
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (reminder_id, occurs_on)
);
