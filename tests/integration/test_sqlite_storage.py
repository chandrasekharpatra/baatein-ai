import asyncio
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.migrate import migrate
from baatein.storage.sqlite.repos import (
    SqliteFollowUpRepo,
    SqliteSessionRepo,
    SqliteTurnRepo,
    SqliteUserRepo,
    fts_query,
    to_db,
)
from tests.factories import make_followup, make_session, make_turn, make_user


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    path = tmp_path / "app.db"
    migrate(path)
    database = Database(path)
    yield database
    await database.close()


async def seeded_session(db: Database) -> tuple[str, str]:
    user = make_user()
    await SqliteUserRepo(db).add(user)
    session = make_session(user.id)
    await SqliteSessionRepo(db).add(session)
    return user.id, session.id


async def test_transaction_rolls_back_on_error(db: Database) -> None:
    user = make_user()

    async def insert_then_fail() -> None:
        async with db.transaction() as conn:
            await conn.execute(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user.id, user.email, "h", to_db(user.created_at)),
            )
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await insert_then_fail()

    assert await SqliteUserRepo(db).get(user.id) is None


async def test_foreign_keys_are_enforced(db: Database) -> None:
    async def insert_orphan() -> None:
        async with db.transaction() as conn:
            await conn.execute(
                "INSERT INTO followups (id, user_id, text, created_at)"
                " VALUES ('f', 'nope', 't', 'x')"
            )

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        await insert_orphan()


async def test_close_then_reuse_reconnects(db: Database) -> None:
    user = make_user()
    await SqliteUserRepo(db).add(user)

    await db.close()
    await db.close()

    assert await SqliteUserRepo(db).get(user.id) == user
    assert db.path.endswith("app.db")


async def test_concurrent_writes_are_serialized(db: Database) -> None:
    _, session_id = await seeded_session(db)
    turns = SqliteTurnRepo(db)

    await asyncio.gather(*(turns.append(make_turn(session_id, seq)) for seq in range(1, 51)))

    assert [t.seq for t in await turns.list_for_session(session_id)] == list(range(1, 51))


async def test_deleting_user_cascades_and_cleans_search_index(db: Database) -> None:
    user_id, session_id = await seeded_session(db)
    await SqliteTurnRepo(db).append(make_turn(session_id, 1, "secret plans"))
    await SqliteFollowUpRepo(db).add(make_followup(user_id))

    async with db.transaction() as conn:
        await conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        counts = [
            (await (await conn.execute(f"SELECT count(*) FROM {table}")).fetchone())[0]  # type: ignore[index]
            for table in ("sessions", "turns", "followups")
        ]
        fts = await (
            await conn.execute("SELECT count(*) FROM turns_fts WHERE turns_fts MATCH 'secret'")
        ).fetchone()

    assert counts == [0, 0, 0]
    assert fts is not None
    assert fts[0] == 0


async def test_search_handles_devanagari(db: Database) -> None:
    user_id, session_id = await seeded_session(db)
    turns = SqliteTurnRepo(db)
    hit = await turns.append(make_turn(session_id, 1, "मेरी बहन की शादी दिसंबर में है"))
    await turns.append(make_turn(session_id, 2, "आज मौसम अच्छा है"))

    results = await turns.search(user_id, "शादी कब है", limit=1)

    assert [t.id for t in results] == [hit.id]


async def test_search_ignores_fts_syntax_in_user_input(db: Database) -> None:
    user_id, session_id = await seeded_session(db)
    turns = SqliteTurnRepo(db)
    hit = await turns.append(make_turn(session_id, 1, "NEAR the AND office"))

    results = await turns.search(user_id, 'office" OR * NEAR(', limit=5)

    assert [t.id for t in results] == [hit.id]


async def test_naive_datetimes_are_rejected(db: Database) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        await SqliteUserRepo(db).add(make_user(created_at=datetime(2026, 1, 1)))


async def test_unexpected_integrity_errors_propagate(db: Database) -> None:
    user_id, session_id = await seeded_session(db)
    user = await SqliteUserRepo(db).get(user_id)
    session = await SqliteSessionRepo(db).get(session_id)
    followup = make_followup(user_id)
    await SqliteFollowUpRepo(db).add(followup)
    assert user is not None
    assert session is not None

    with pytest.raises(sqlite3.IntegrityError):
        await SqliteUserRepo(db).add(replace(user, email="new@example.com"))
    with pytest.raises(sqlite3.IntegrityError):
        await SqliteSessionRepo(db).add(session)
    with pytest.raises(sqlite3.IntegrityError):
        await SqliteFollowUpRepo(db).add(followup)
    async with db.transaction() as conn:
        await conn.execute(
            "CREATE TRIGGER reject_turns BEFORE INSERT ON turns BEGIN"
            " SELECT RAISE(ABORT, 'rejected by test'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="rejected by test"):
        await SqliteTurnRepo(db).append(make_turn(session_id, 1))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hello world", '"hello" OR "world"'),
        ('say "hi"', '"say" OR "hi"'),
        ("?? !!", None),
        ("", None),
    ],
)
def test_fts_query(text: str, expected: str | None) -> None:
    assert fts_query(text) == expected
