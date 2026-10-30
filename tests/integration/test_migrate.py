import sqlite3
from pathlib import Path

import pytest

from baatein.storage.sqlite.migrate import (
    Migration,
    MigrationError,
    bundled_migrations,
    migrate,
    parse_migrations,
    split_statements,
)


def tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')")
        return {name for (name,) in rows}


def test_fresh_database_migrates_to_latest(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "app.db"

    version = migrate(path)

    assert version == len(bundled_migrations())
    assert {"users", "sessions", "turns", "turns_fts", "threads", "turns_ai"} <= tables(path)
    assert "followups" not in tables(path)


def test_followups_become_threads(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    migrate(path, bundled_migrations()[:3])
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES ('u', 'e', 'h', 't')"
        )
        conn.executemany(
            "INSERT INTO followups (id, user_id, text, due_at, status, created_at)"
            " VALUES (?, 'u', ?, ?, ?, 't')",
            [
                ("f1", "Ask about the interview", "2026-11-06", "open"),
                ("f2", "Ask about the flat", None, "done"),
                ("f3", "Ask about the trip", None, "dropped"),
            ],
        )

    migrate(path)

    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT id, title, status, check_in_after, kind FROM threads ORDER BY id"
        ).fetchall()
        timezone = conn.execute("SELECT timezone FROM users").fetchone()[0]
    assert rows == [
        ("f1", "Ask about the interview", "open", "2026-11-06", "plan"),
        ("f2", "Ask about the flat", "done", None, "plan"),
        ("f3", "Ask about the trip", "cancelled", None, "plan"),
    ]
    assert timezone == "Asia/Kolkata"


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "app.db"

    assert migrate(path) == migrate(path)


def test_migrate_uses_wal(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    migrate(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_migrate_in_memory() -> None:
    assert migrate(":memory:") == len(bundled_migrations())


def test_only_pending_migrations_are_applied(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    first = Migration(1, "a", "CREATE TABLE a (x INTEGER);")
    second = Migration(2, "b", "CREATE TABLE b (x INTEGER);")
    migrate(path, [first])

    assert migrate(path, [first, second]) == 2
    assert {"a", "b"} <= tables(path)


def test_failed_migration_rolls_back_and_keeps_version(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    good = Migration(1, "good", "CREATE TABLE good (x INTEGER);")
    bad = Migration(2, "bad", "CREATE TABLE partial (x INTEGER);\nSELECT * FROM nope;")

    with pytest.raises(MigrationError, match="0002_bad"):
        migrate(path, [good, bad])

    assert "partial" not in tables(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_split_statements_keeps_trigger_bodies_together() -> None:
    sql = """
CREATE TABLE t (x INTEGER);
CREATE TRIGGER tr AFTER INSERT ON t BEGIN
    INSERT INTO t (x) VALUES (1);
END;
"""
    statements = split_statements(sql)

    assert len(statements) == 2
    assert statements[1].startswith("CREATE TRIGGER")
    assert statements[1].endswith("END;")


def test_split_statements_rejects_incomplete_sql() -> None:
    with pytest.raises(MigrationError, match="incomplete"):
        split_statements("CREATE TABLE t (x INTEGER)")


def test_parse_migrations_sorts_by_version() -> None:
    parsed = parse_migrations([("0002_b.sql", "b"), ("0001_a.sql", "a")])

    assert [(m.version, m.name, m.sql) for m in parsed] == [(1, "a", "a"), (2, "b", "b")]


@pytest.mark.parametrize("filename", ["1_init.sql", "0001-init.sql", "0001_Init.sql", "x.sql"])
def test_parse_migrations_rejects_bad_filenames(filename: str) -> None:
    with pytest.raises(MigrationError, match="invalid migration filename"):
        parse_migrations([(filename, "")])


@pytest.mark.parametrize(
    "names",
    [["0002_b.sql"], ["0001_a.sql", "0003_c.sql"], ["0001_a.sql", "0001_b.sql"]],
)
def test_parse_migrations_requires_contiguous_versions(names: list[str]) -> None:
    with pytest.raises(MigrationError, match="contiguous"):
        parse_migrations([(name, "") for name in names])
