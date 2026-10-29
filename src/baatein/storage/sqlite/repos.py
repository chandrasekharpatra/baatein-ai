import json
import sqlite3
from datetime import UTC, date, datetime, time

import aiosqlite

from baatein.domain.errors import (
    DuplicateEmailError,
    DuplicatePersonError,
    DuplicateTurnError,
    NotFoundError,
)
from baatein.domain.models import (
    Boundary,
    BoundaryScope,
    DeclinedSuggestion,
    FinalizeStatus,
    OccurrenceStatus,
    Pace,
    Person,
    Preferences,
    Reminder,
    ReminderStatus,
    Repeat,
    Role,
    Sensitivity,
    Session,
    Thread,
    ThreadEvent,
    ThreadKind,
    ThreadStatus,
    Turn,
    User,
    Verbosity,
)
from baatein.storage.sqlite.db import Database
from baatein.text import search_terms


def to_db(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError(f"naive datetime {value!r}; timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def from_db(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def required(value: str) -> datetime:
    return datetime.fromisoformat(value)


def fts_query(text: str) -> str | None:
    return " OR ".join(f'"{term}"' for term in search_terms(text)) or None


def is_unique_violation(exc: sqlite3.IntegrityError) -> bool:
    return "UNIQUE constraint failed" in str(exc)


def is_fk_violation(exc: sqlite3.IntegrityError) -> bool:
    return "FOREIGN KEY constraint failed" in str(exc)


def ensure_changed(cursor: aiosqlite.Cursor, entity: str, entity_id: str) -> None:
    if cursor.rowcount == 0:
        raise NotFoundError(entity, entity_id)


class SqliteUserRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, user: User) -> None:
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO users (id, email, password_hash, display_name, language,"
                    " timezone, persona, address_as, pace, verbosity, fewer_questions,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user.id,
                        user.email,
                        user.password_hash,
                        user.display_name,
                        user.language,
                        user.timezone,
                        user.persona,
                        *_preference_values(user.preferences),
                        to_db(user.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if is_unique_violation(exc) and "users.email" in str(exc):
                raise DuplicateEmailError(user.email) from exc
            raise

    async def get(self, user_id: str) -> User | None:
        return await self._one("SELECT * FROM users WHERE id = ?", user_id)

    async def get_by_email(self, email: str) -> User | None:
        return await self._one("SELECT * FROM users WHERE email = ?", email)

    async def set_preferences(self, user_id: str, preferences: Preferences) -> None:
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE users SET address_as = ?, pace = ?, verbosity = ?, fewer_questions = ?"
                " WHERE id = ?",
                (*_preference_values(preferences), user_id),
            )
            ensure_changed(cursor, "user", user_id)

    async def set_persona(self, user_id: str, persona: str) -> None:
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE users SET persona = ? WHERE id = ?", (persona, user_id)
            )
            ensure_changed(cursor, "user", user_id)

    async def _one(self, sql: str, param: str) -> User | None:
        async with self._db.read() as conn, conn.execute(sql, (param,)) as cursor:
            row = await cursor.fetchone()
        return None if row is None else _user(row)


class SqliteSessionRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, session: Session) -> None:
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO sessions (id, user_id, room_name, started_at, ended_at,"
                    " end_reason, summary, finalize_status, finalize_attempts, memory_synced_seq,"
                    " ended_abruptly, unfinished_topic)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session.id,
                        session.user_id,
                        session.room_name,
                        to_db(session.started_at),
                        to_db(session.ended_at),
                        session.end_reason,
                        session.summary,
                        session.finalize_status.value,
                        session.finalize_attempts,
                        session.memory_synced_seq,
                        int(session.ended_abruptly),
                        session.unfinished_topic,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if is_fk_violation(exc):
                raise NotFoundError("user", session.user_id) from exc
            raise

    async def get(self, session_id: str) -> Session | None:
        sessions = await self._many("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return sessions[0] if sessions else None

    async def recent_for_user(self, user_id: str, limit: int) -> list[Session]:
        return await self._many(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY started_at DESC LIMIT ?",
            (user_id, limit),
        )

    async def end(
        self,
        session_id: str,
        ended_at: datetime,
        reason: str | None,
        ended_abruptly: bool = False,
        unfinished_topic: str | None = None,
    ) -> None:
        await self._update(
            session_id,
            "UPDATE sessions SET ended_at = ?, end_reason = ?, finalize_status = 'pending',"
            " ended_abruptly = ?, unfinished_topic = ? WHERE id = ?",
            (to_db(ended_at), reason, int(ended_abruptly), unfinished_topic, session_id),
        )

    async def set_memory_synced_seq(self, session_id: str, seq: int) -> None:
        await self._update(
            session_id,
            "UPDATE sessions SET memory_synced_seq = ? WHERE id = ?",
            (seq, session_id),
        )

    async def mark_finalized(self, session_id: str, summary: str | None) -> None:
        await self._update(
            session_id,
            "UPDATE sessions SET finalize_status = 'done', summary = ? WHERE id = ?",
            (summary, session_id),
        )

    async def mark_finalize_failed(self, session_id: str) -> None:
        await self._update(
            session_id,
            "UPDATE sessions SET finalize_status = 'failed',"
            " finalize_attempts = finalize_attempts + 1 WHERE id = ?",
            (session_id,),
        )

    async def pending_finalization(self, max_attempts: int, limit: int) -> list[Session]:
        return await self._many(
            "SELECT * FROM sessions WHERE finalize_status IN ('pending', 'failed')"
            " AND finalize_attempts < ? ORDER BY ended_at, id LIMIT ?",
            (max_attempts, limit),
        )

    async def clear_summary(self, session_id: str) -> None:
        await self._update(
            session_id,
            "UPDATE sessions SET summary = NULL, unfinished_topic = NULL WHERE id = ?",
            (session_id,),
        )

    async def _update(self, session_id: str, sql: str, params: tuple[object, ...]) -> None:
        async with self._db.transaction() as conn:
            cursor = await conn.execute(sql, params)
            ensure_changed(cursor, "session", session_id)

    async def _many(self, sql: str, params: tuple[object, ...]) -> list[Session]:
        async with self._db.read() as conn, conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_session(row) for row in rows]


class SqliteTurnRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(self, turn: Turn) -> Turn:
        try:
            async with self._db.transaction() as conn:
                cursor = await conn.execute(
                    "INSERT INTO turns (session_id, seq, role, text, language, interrupted,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        turn.session_id,
                        turn.seq,
                        turn.role.value,
                        turn.text,
                        turn.language,
                        int(turn.interrupted),
                        to_db(turn.created_at),
                    ),
                )
                turn_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            if is_fk_violation(exc):
                raise NotFoundError("session", turn.session_id) from exc
            if is_unique_violation(exc):
                raise DuplicateTurnError(turn.session_id, turn.seq) from exc
            raise
        return Turn(
            id=turn_id,
            session_id=turn.session_id,
            seq=turn.seq,
            role=turn.role,
            text=turn.text,
            created_at=turn.created_at,
            language=turn.language,
            interrupted=turn.interrupted,
        )

    async def list_for_session(self, session_id: str, after_seq: int = 0) -> list[Turn]:
        return await self._many(
            "SELECT * FROM turns WHERE session_id = ? AND seq > ? ORDER BY seq",
            (session_id, after_seq),
        )

    async def search(self, user_id: str, query: str, limit: int) -> list[Turn]:
        match = fts_query(query)
        if match is None:
            return []
        return await self._many(
            "SELECT turns.* FROM turns_fts"
            " JOIN turns ON turns.id = turns_fts.rowid"
            " JOIN sessions ON sessions.id = turns.session_id"
            " WHERE turns_fts MATCH ? AND sessions.user_id = ?"
            " ORDER BY bm25(turns_fts), turns.created_at DESC LIMIT ?",
            (match, user_id, limit),
        )

    async def redact_session(self, session_id: str, text: str) -> int:
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE turns SET text = ? WHERE session_id = ?", (text, session_id)
            )
            return cursor.rowcount

    async def _many(self, sql: str, params: tuple[object, ...]) -> list[Turn]:
        async with self._db.read() as conn, conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_turn(row) for row in rows]


class SqlitePeopleRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, person: Person) -> None:
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO people (id, user_id, name, relation, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        person.id,
                        person.user_id,
                        person.name,
                        person.relation,
                        to_db(person.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if is_fk_violation(exc):
                raise NotFoundError("user", person.user_id) from exc
            if is_unique_violation(exc):
                raise DuplicatePersonError(person.name) from exc
            raise

    async def get(self, person_id: str) -> Person | None:
        people = await self._many("SELECT * FROM people WHERE id = ?", (person_id,))
        return people[0] if people else None

    async def find_by_name(self, user_id: str, name: str) -> Person | None:
        people = await self._many(
            "SELECT * FROM people WHERE user_id = ? AND name = ? COLLATE NOCASE",
            (user_id, name.strip()),
        )
        return people[0] if people else None

    async def list_for_user(self, user_id: str, limit: int) -> list[Person]:
        return await self._many(
            "SELECT * FROM people WHERE user_id = ? ORDER BY created_at, name LIMIT ?",
            (user_id, limit),
        )

    async def update(self, person: Person) -> None:
        try:
            async with self._db.transaction() as conn:
                cursor = await conn.execute(
                    "UPDATE people SET name = ?, relation = ? WHERE id = ?",
                    (person.name, person.relation, person.id),
                )
                ensure_changed(cursor, "person", person.id)
        except sqlite3.IntegrityError as exc:
            if is_unique_violation(exc):
                raise DuplicatePersonError(person.name) from exc
            raise

    async def delete(self, person_id: str) -> None:
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM people WHERE id = ?", (person_id,))

    async def _many(self, sql: str, params: tuple[object, ...]) -> list[Person]:
        async with self._db.read() as conn, conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_person(row) for row in rows]


class SqliteThreadRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, thread: Thread) -> None:
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO threads (id, user_id, kind, title, status, next_step,"
                    " check_in_after, last_offered_at, times_offered, sensitivity,"
                    " about_person_id, source_session_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        thread.id,
                        thread.user_id,
                        thread.kind.value,
                        thread.title,
                        thread.status.value,
                        thread.next_step,
                        to_db(thread.check_in_after),
                        to_db(thread.last_offered_at),
                        thread.times_offered,
                        thread.sensitivity.value,
                        thread.about_person_id,
                        thread.source_session_id,
                        to_db(thread.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if is_fk_violation(exc):
                raise NotFoundError("user, person or session", thread.user_id) from exc
            raise

    async def get(self, thread_id: str) -> Thread | None:
        threads = await self._many("SELECT * FROM threads WHERE id = ?", (thread_id,))
        return threads[0] if threads else None

    async def update(self, thread: Thread) -> None:
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE threads SET kind = ?, title = ?, status = ?, next_step = ?,"
                " check_in_after = ?, last_offered_at = ?, times_offered = ?, sensitivity = ?,"
                " about_person_id = ? WHERE id = ?",
                (
                    thread.kind.value,
                    thread.title,
                    thread.status.value,
                    thread.next_step,
                    to_db(thread.check_in_after),
                    to_db(thread.last_offered_at),
                    thread.times_offered,
                    thread.sensitivity.value,
                    thread.about_person_id,
                    thread.id,
                ),
            )
            ensure_changed(cursor, "thread", thread.id)

    async def list_active(self, user_id: str, limit: int) -> list[Thread]:
        return await self._many(
            "SELECT * FROM threads WHERE user_id = ?"
            " AND status IN ('open', 'in_progress', 'snoozed')"
            " ORDER BY created_at, id LIMIT ?",
            (user_id, limit),
        )

    async def add_event(self, event: ThreadEvent) -> None:
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO thread_events (id, thread_id, text, status, session_id,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event.id,
                        event.thread_id,
                        event.text,
                        event.status.value if event.status else None,
                        event.session_id,
                        to_db(event.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if is_fk_violation(exc):
                raise NotFoundError("thread or session", event.thread_id) from exc
            raise

    async def events(self, thread_id: str, limit: int) -> list[ThreadEvent]:
        async with (
            self._db.read() as conn,
            conn.execute(
                "SELECT * FROM (SELECT * FROM thread_events WHERE thread_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT ?) ORDER BY created_at, id",
                (thread_id, limit),
            ) as cursor,
        ):
            rows = await cursor.fetchall()
        return [_thread_event(row) for row in rows]

    async def count_about(self, person_id: str) -> int:
        async with (
            self._db.read() as conn,
            conn.execute(
                "SELECT count(*) FROM threads WHERE about_person_id = ?", (person_id,)
            ) as cursor,
        ):
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def delete(self, thread_id: str) -> list[str]:
        async with self._db.transaction() as conn:
            async with conn.execute(
                "SELECT source_session_id FROM threads WHERE id = ?"
                " UNION SELECT session_id FROM thread_events WHERE thread_id = ?",
                (thread_id, thread_id),
            ) as cursor:
                rows = await cursor.fetchall()
            await conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        return sorted(row[0] for row in rows if row[0])

    async def _many(self, sql: str, params: tuple[object, ...]) -> list[Thread]:
        async with self._db.read() as conn, conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_thread(row) for row in rows]


class SqliteBoundaryRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, boundary: Boundary) -> None:
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO boundaries (id, user_id, topic, scope, session_id, until,"
                    " thread_id, created_at, lifted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        boundary.id,
                        boundary.user_id,
                        boundary.topic,
                        boundary.scope.value,
                        boundary.session_id,
                        to_db(boundary.until),
                        boundary.thread_id,
                        to_db(boundary.created_at),
                        to_db(boundary.lifted_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if is_fk_violation(exc):
                raise NotFoundError("user, session or thread", boundary.user_id) from exc
            raise

    async def get(self, boundary_id: str) -> Boundary | None:
        boundaries = await self._many("SELECT * FROM boundaries WHERE id = ?", (boundary_id,))
        return boundaries[0] if boundaries else None

    async def list_active(self, user_id: str, session_id: str, now: datetime) -> list[Boundary]:
        return await self._many(
            "SELECT * FROM boundaries WHERE user_id = ? AND lifted_at IS NULL AND ("
            " scope = 'until_reopened'"
            " OR (scope = 'this_call' AND session_id = ?)"
            " OR (scope = 'until_date' AND until > ?)"
            ") ORDER BY created_at, id",
            (user_id, session_id, to_db(now)),
        )

    async def lift(self, boundary_id: str, at: datetime) -> None:
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE boundaries SET lifted_at = COALESCE(lifted_at, ?) WHERE id = ?",
                (to_db(at), boundary_id),
            )
            ensure_changed(cursor, "boundary", boundary_id)

    async def _many(self, sql: str, params: tuple[object, ...]) -> list[Boundary]:
        async with self._db.read() as conn, conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_boundary(row) for row in rows]


class SqliteSuggestionRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, suggestion: DeclinedSuggestion) -> None:
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO declined_suggestions (id, user_id, text, session_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        suggestion.id,
                        suggestion.user_id,
                        suggestion.text,
                        suggestion.session_id,
                        to_db(suggestion.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if is_fk_violation(exc):
                raise NotFoundError("user or session", suggestion.user_id) from exc
            raise

    async def list_for_user(self, user_id: str, limit: int) -> list[DeclinedSuggestion]:
        async with (
            self._db.read() as conn,
            conn.execute(
                "SELECT * FROM declined_suggestions WHERE user_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (user_id, limit),
            ) as cursor,
        ):
            rows = await cursor.fetchall()
        return [_suggestion(row) for row in rows]


class SqliteReminderRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, reminder: Reminder) -> None:
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO reminders (id, user_id, title, event_date, local_time, repeat,"
                    " alert_days_before, status, note, about_person_id, source_session_id,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        reminder.id,
                        reminder.user_id,
                        *_reminder_values(reminder),
                        reminder.source_session_id,
                        to_db(reminder.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if is_fk_violation(exc):
                raise NotFoundError("user, person or session", reminder.user_id) from exc
            raise

    async def get(self, reminder_id: str) -> Reminder | None:
        reminders = await self._many("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        return reminders[0] if reminders else None

    async def update(self, reminder: Reminder) -> None:
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE reminders SET title = ?, event_date = ?, local_time = ?, repeat = ?,"
                " alert_days_before = ?, status = ?, note = ?, about_person_id = ? WHERE id = ?",
                (*_reminder_values(reminder), reminder.id),
            )
            ensure_changed(cursor, "reminder", reminder.id)

    async def list_open(self, user_id: str, limit: int) -> list[Reminder]:
        return await self._many(
            "SELECT * FROM reminders WHERE user_id = ? AND status IN ('needs_info', 'active')"
            " ORDER BY created_at, id LIMIT ?",
            (user_id, limit),
        )

    async def count_about(self, person_id: str) -> int:
        async with (
            self._db.read() as conn,
            conn.execute(
                "SELECT count(*) FROM reminders WHERE about_person_id = ?", (person_id,)
            ) as cursor,
        ):
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def settle(
        self, reminder_id: str, on: date, status: OccurrenceStatus, at: datetime
    ) -> None:
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO reminder_occurrences (reminder_id, occurs_on, status, updated_at)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT (reminder_id, occurs_on)"
                    " DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
                    (reminder_id, on.isoformat(), status.value, to_db(at)),
                )
        except sqlite3.IntegrityError as exc:
            if is_fk_violation(exc):
                raise NotFoundError("reminder", reminder_id) from exc
            raise

    async def settled(self, reminder_id: str) -> dict[date, OccurrenceStatus]:
        async with (
            self._db.read() as conn,
            conn.execute(
                "SELECT occurs_on, status FROM reminder_occurrences WHERE reminder_id = ?",
                (reminder_id,),
            ) as cursor,
        ):
            rows = await cursor.fetchall()
        return {date.fromisoformat(row[0]): OccurrenceStatus(row[1]) for row in rows}

    async def _many(self, sql: str, params: tuple[object, ...]) -> list[Reminder]:
        async with self._db.read() as conn, conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_reminder(row) for row in rows]


def _reminder_values(reminder: Reminder) -> tuple[object, ...]:
    return (
        reminder.title,
        reminder.on.isoformat() if reminder.on else None,
        reminder.at.strftime("%H:%M"),
        reminder.repeat.value,
        json.dumps(list(reminder.alert_days_before)),
        reminder.status.value,
        reminder.note,
        reminder.about_person_id,
    )


def _reminder(row: aiosqlite.Row) -> Reminder:
    return Reminder(
        id=row["id"],
        user_id=row["user_id"],
        title=row["title"],
        created_at=required(row["created_at"]),
        on=date.fromisoformat(row["event_date"]) if row["event_date"] else None,
        at=time.fromisoformat(row["local_time"]),
        repeat=Repeat(row["repeat"]),
        alert_days_before=tuple(json.loads(row["alert_days_before"])),
        status=ReminderStatus(row["status"]),
        note=row["note"],
        about_person_id=row["about_person_id"],
        source_session_id=row["source_session_id"],
    )


def _thread(row: aiosqlite.Row) -> Thread:
    return Thread(
        id=row["id"],
        user_id=row["user_id"],
        title=row["title"],
        created_at=required(row["created_at"]),
        kind=ThreadKind(row["kind"]),
        status=ThreadStatus(row["status"]),
        next_step=row["next_step"],
        check_in_after=from_db(row["check_in_after"]),
        last_offered_at=from_db(row["last_offered_at"]),
        times_offered=row["times_offered"],
        sensitivity=Sensitivity(row["sensitivity"]),
        about_person_id=row["about_person_id"],
        source_session_id=row["source_session_id"],
    )


def _thread_event(row: aiosqlite.Row) -> ThreadEvent:
    return ThreadEvent(
        id=row["id"],
        thread_id=row["thread_id"],
        text=row["text"],
        created_at=required(row["created_at"]),
        status=ThreadStatus(row["status"]) if row["status"] else None,
        session_id=row["session_id"],
    )


def _boundary(row: aiosqlite.Row) -> Boundary:
    return Boundary(
        id=row["id"],
        user_id=row["user_id"],
        topic=row["topic"],
        created_at=required(row["created_at"]),
        scope=BoundaryScope(row["scope"]),
        session_id=row["session_id"],
        until=from_db(row["until"]),
        thread_id=row["thread_id"],
        lifted_at=from_db(row["lifted_at"]),
    )


def _suggestion(row: aiosqlite.Row) -> DeclinedSuggestion:
    return DeclinedSuggestion(
        id=row["id"],
        user_id=row["user_id"],
        text=row["text"],
        created_at=required(row["created_at"]),
        session_id=row["session_id"],
    )


async def delete_user(db: Database, email: str) -> bool:
    """Remove a user and everything they own; rows cascade and triggers clean the indexes."""
    async with db.transaction() as conn:
        cursor = await conn.execute("DELETE FROM users WHERE email = ?", (email.strip().lower(),))
        return cursor.rowcount > 0


def _person(row: aiosqlite.Row) -> Person:
    return Person(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        created_at=required(row["created_at"]),
        relation=row["relation"],
    )


def _user(row: aiosqlite.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=required(row["created_at"]),
        display_name=row["display_name"],
        language=row["language"],
        timezone=row["timezone"],
        persona=row["persona"],
        preferences=Preferences(
            address_as=row["address_as"],
            pace=Pace(row["pace"]) if row["pace"] else None,
            verbosity=Verbosity(row["verbosity"]) if row["verbosity"] else None,
            fewer_questions=bool(row["fewer_questions"]),
        ),
    )


def _preference_values(preferences: Preferences) -> tuple[object, ...]:
    return (
        preferences.address_as,
        preferences.pace.value if preferences.pace else None,
        preferences.verbosity.value if preferences.verbosity else None,
        int(preferences.fewer_questions),
    )


def _session(row: aiosqlite.Row) -> Session:
    return Session(
        id=row["id"],
        user_id=row["user_id"],
        started_at=required(row["started_at"]),
        room_name=row["room_name"],
        ended_at=from_db(row["ended_at"]),
        end_reason=row["end_reason"],
        summary=row["summary"],
        finalize_status=FinalizeStatus(row["finalize_status"]),
        finalize_attempts=row["finalize_attempts"],
        memory_synced_seq=row["memory_synced_seq"],
        ended_abruptly=bool(row["ended_abruptly"]),
        unfinished_topic=row["unfinished_topic"],
    )


def _turn(row: aiosqlite.Row) -> Turn:
    return Turn(
        id=row["id"],
        session_id=row["session_id"],
        seq=row["seq"],
        role=Role(row["role"]),
        text=row["text"],
        created_at=required(row["created_at"]),
        language=row["language"],
        interrupted=bool(row["interrupted"]),
    )
