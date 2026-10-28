import sqlite3

import aiosqlite
import sqlite_vec


def load_vec(conn: sqlite3.Connection) -> None:
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


async def load_vec_async(conn: aiosqlite.Connection) -> None:
    await conn.enable_load_extension(True)
    try:
        await conn.load_extension(sqlite_vec.loadable_path())  # type: ignore[no-untyped-call]
    finally:
        await conn.enable_load_extension(False)
