import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

_FILENAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def parse_migrations(files: Iterable[tuple[str, str]]) -> list[Migration]:
    migrations = []
    for filename, sql in files:
        match = _FILENAME.match(filename)
        if match is None:
            raise MigrationError(f"invalid migration filename '{filename}'")
        migrations.append(Migration(int(match[1]), match[2], sql))
    migrations.sort(key=lambda m: m.version)

    expected = list(range(1, len(migrations) + 1))
    if [m.version for m in migrations] != expected:
        raise MigrationError(
            f"migration versions must be contiguous from 1, got {[m.version for m in migrations]}"
        )
    return migrations


def bundled_migrations() -> list[Migration]:
    package = resources.files("baatein.storage.sqlite.migrations")
    return parse_migrations(
        (entry.name, entry.read_text(encoding="utf-8"))
        for entry in package.iterdir()
        if entry.name.endswith(".sql")
    )


def split_statements(sql: str) -> list[str]:
    statements, buffer = [], ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise MigrationError(f"incomplete SQL statement: {buffer.strip()[:80]!r}")
    return statements


def current_version(conn: sqlite3.Connection) -> int:
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    return version


def migrate(path: Path | str, migrations: Sequence[Migration] | None = None) -> int:
    migrations = bundled_migrations() if migrations is None else migrations
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        for migration in migrations:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if current_version(conn) >= migration.version:
                    conn.execute("COMMIT")
                    continue
                for statement in split_statements(migration.sql):
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {migration.version}")
                conn.execute("COMMIT")
            except Exception as exc:
                conn.execute("ROLLBACK")
                raise MigrationError(
                    f"migration {migration.version:04d}_{migration.name} failed: {exc}"
                ) from exc
        return current_version(conn)
    finally:
        conn.close()
