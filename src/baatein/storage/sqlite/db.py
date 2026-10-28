import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


class Database:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def path(self) -> str:
        return self._path

    async def _connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            conn = await aiosqlite.connect(self._path, isolation_level=None)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute("PRAGMA busy_timeout = 5000")
            await conn.execute("PRAGMA synchronous = NORMAL")
            self._conn = conn
        return self._conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            conn = await self._connection()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                await conn.execute("ROLLBACK")
                raise
            await conn.execute("COMMIT")

    @asynccontextmanager
    async def read(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            yield await self._connection()

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None
