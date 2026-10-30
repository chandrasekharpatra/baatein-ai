"""Whole-account reads and writes for the admin tools: list every account, read everything one
account owns (to download it as a seed file), and swap a freshly loaded account in for an old
one in a single transaction."""

from dataclasses import dataclass
from datetime import date, datetime

from baatein.domain.models import (
    Boundary,
    DeclinedSuggestion,
    Fact,
    OccurrenceStatus,
    Person,
    Reminder,
    Session,
    Thread,
    ThreadEvent,
    Turn,
    User,
)
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.memory_store import _fact
from baatein.storage.sqlite.repos import (
    _boundary,
    _person,
    _reminder,
    _session,
    _suggestion,
    _thread,
    _thread_event,
    _turn,
    _user,
    from_db,
)


@dataclass(frozen=True)
class AccountRow:
    user: User
    memories: int
    calls: int
    last_call_at: datetime | None


@dataclass(frozen=True)
class AccountData:
    """Everything an account owns, oldest first."""

    user: User
    people: list[Person]
    sessions: list[Session]
    turns: dict[str, list[Turn]]
    facts: list[Fact]
    threads: list[Thread]
    events: dict[str, list[ThreadEvent]]
    boundaries: list[Boundary]
    declined: list[DeclinedSuggestion]
    reminders: list[Reminder]
    settled: dict[str, dict[date, OccurrenceStatus]]


async def list_accounts(db: Database) -> list[AccountRow]:
    sql = (
        "SELECT u.*,"
        " (SELECT count(*) FROM facts f WHERE f.user_id = u.id"
        "  AND f.retired_at IS NULL AND f.deleted_at IS NULL) AS memories,"
        " (SELECT count(*) FROM sessions s WHERE s.user_id = u.id) AS calls,"
        " (SELECT max(started_at) FROM sessions s WHERE s.user_id = u.id) AS last_call_at"
        " FROM users u ORDER BY u.email"
    )
    async with db.read() as conn, conn.execute(sql) as cursor:
        rows = await cursor.fetchall()
    return [
        AccountRow(_user(row), row["memories"], row["calls"], from_db(row["last_call_at"]))
        for row in rows
    ]


async def read_account(db: Database, user_id: str) -> AccountData | None:
    async with db.read() as conn:

        async def rows(sql: str, *params: object) -> list:  # type: ignore[type-arg]
            async with conn.execute(sql, params) as cursor:
                return list(await cursor.fetchall())

        users = await rows("SELECT * FROM users WHERE id = ?", user_id)
        if not users:
            return None
        sessions = [
            _session(row)
            for row in await rows(
                "SELECT * FROM sessions WHERE user_id = ? ORDER BY started_at, rowid", user_id
            )
        ]
        turns: dict[str, list[Turn]] = {}
        for row in await rows(
            "SELECT t.* FROM turns t JOIN sessions s ON s.id = t.session_id"
            " WHERE s.user_id = ? ORDER BY t.session_id, t.seq",
            user_id,
        ):
            turn = _turn(row)
            turns.setdefault(turn.session_id, []).append(turn)
        threads = [
            _thread(row)
            for row in await rows(
                "SELECT * FROM threads WHERE user_id = ? ORDER BY created_at, rowid", user_id
            )
        ]
        events: dict[str, list[ThreadEvent]] = {}
        for row in await rows(
            "SELECT e.* FROM thread_events e JOIN threads t ON t.id = e.thread_id"
            " WHERE t.user_id = ? ORDER BY e.created_at, e.rowid",
            user_id,
        ):
            event = _thread_event(row)
            events.setdefault(event.thread_id, []).append(event)
        reminders = [
            _reminder(row)
            for row in await rows(
                "SELECT * FROM reminders WHERE user_id = ? ORDER BY created_at, rowid", user_id
            )
        ]
        settled: dict[str, dict[date, OccurrenceStatus]] = {}
        for row in await rows(
            "SELECT o.* FROM reminder_occurrences o JOIN reminders r ON r.id = o.reminder_id"
            " WHERE r.user_id = ? ORDER BY o.reminder_id, o.occurs_on",
            user_id,
        ):
            settled.setdefault(row["reminder_id"], {})[date.fromisoformat(row["occurs_on"])] = (
                OccurrenceStatus(row["status"])
            )
        return AccountData(
            user=_user(users[0]),
            people=[
                _person(row)
                for row in await rows(
                    "SELECT * FROM people WHERE user_id = ? ORDER BY created_at, rowid", user_id
                )
            ],
            sessions=sessions,
            turns=turns,
            facts=[
                _fact(row)
                for row in await rows(
                    "SELECT * FROM facts WHERE user_id = ? ORDER BY created_at, pk", user_id
                )
            ],
            threads=threads,
            events=events,
            boundaries=[
                _boundary(row)
                for row in await rows(
                    "SELECT * FROM boundaries WHERE user_id = ? ORDER BY created_at, rowid", user_id
                )
            ],
            declined=[
                _suggestion(row)
                for row in await rows(
                    "SELECT * FROM declined_suggestions WHERE user_id = ?"
                    " ORDER BY created_at, rowid",
                    user_id,
                )
            ],
            reminders=reminders,
            settled=settled,
        )


async def take_over(db: Database, user_id: str, email: str) -> bool:
    """Give `user_id` the address `email`, removing whoever had it, in one transaction.

    A replaced account is loaded under a placeholder address first, so if loading fails the
    old account is still there, untouched. Returns whether an old account was removed.
    """
    async with db.transaction() as conn:
        cursor = await conn.execute(
            "DELETE FROM users WHERE email = ? AND id != ?", (email, user_id)
        )
        await conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
        return cursor.rowcount > 0
