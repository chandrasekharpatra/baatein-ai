import sqlite3
from datetime import UTC, datetime

import aiosqlite

from baatein.domain.errors import DuplicateEmailError, DuplicateTurnError, NotFoundError
from baatein.domain.models import (
    FinalizeStatus,
    FollowUp,
    FollowUpStatus,
    Role,
    Session,
    Turn,
    User,
)
from baatein.storage.sqlite.db import Database


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
    terms = [term.replace('"', "") for term in text.split()]
    terms = [term for term in terms if any(ch.isalnum() for ch in term)]
    return " OR ".join(f'"{term}"' for term in terms) or None


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
                    " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        user.id,
                        user.email,
                        user.password_hash,
                        user.display_name,
                        user.language,
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
                    " end_reason, summary, finalize_status, finalize_attempts, memory_synced_seq)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

    async def end(self, session_id: str, ended_at: datetime, reason: str | None) -> None:
        await self._update(
            session_id,
            "UPDATE sessions SET ended_at = ?, end_reason = ?, finalize_status = 'pending'"
            " WHERE id = ?",
            (to_db(ended_at), reason, session_id),
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

    async def _many(self, sql: str, params: tuple[object, ...]) -> list[Turn]:
        async with self._db.read() as conn, conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_turn(row) for row in rows]


class SqliteFollowUpRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, followup: FollowUp) -> None:
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO followups (id, user_id, text, due_at, status, priority,"
                    " source_session_id, created_at, resolved_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        followup.id,
                        followup.user_id,
                        followup.text,
                        to_db(followup.due_at),
                        followup.status.value,
                        followup.priority,
                        followup.source_session_id,
                        to_db(followup.created_at),
                        to_db(followup.resolved_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if is_fk_violation(exc):
                raise NotFoundError("user or session", followup.user_id) from exc
            raise

    async def get(self, followup_id: str) -> FollowUp | None:
        followups = await self._many("SELECT * FROM followups WHERE id = ?", (followup_id,))
        return followups[0] if followups else None

    async def list_open(self, user_id: str, limit: int) -> list[FollowUp]:
        return await self._many(
            "SELECT * FROM followups WHERE user_id = ? AND status = 'open'"
            " ORDER BY due_at IS NULL, due_at, priority DESC, created_at LIMIT ?",
            (user_id, limit),
        )

    async def resolve(
        self, followup_id: str, status: FollowUpStatus, resolved_at: datetime
    ) -> None:
        if status is FollowUpStatus.OPEN:
            raise ValueError("a follow-up can only be resolved as done or dropped")
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE followups SET status = ?, resolved_at = ? WHERE id = ?",
                (status.value, to_db(resolved_at), followup_id),
            )
            ensure_changed(cursor, "follow-up", followup_id)

    async def _many(self, sql: str, params: tuple[object, ...]) -> list[FollowUp]:
        async with self._db.read() as conn, conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_followup(row) for row in rows]


def _user(row: aiosqlite.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=required(row["created_at"]),
        display_name=row["display_name"],
        language=row["language"],
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


def _followup(row: aiosqlite.Row) -> FollowUp:
    return FollowUp(
        id=row["id"],
        user_id=row["user_id"],
        text=row["text"],
        created_at=required(row["created_at"]),
        due_at=from_db(row["due_at"]),
        status=FollowUpStatus(row["status"]),
        priority=row["priority"],
        source_session_id=row["source_session_id"],
        resolved_at=from_db(row["resolved_at"]),
    )
