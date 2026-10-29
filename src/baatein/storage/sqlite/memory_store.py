import re
import sqlite3
from datetime import datetime

import aiosqlite
from sqlite_vec import serialize_float32

from baatein.domain.errors import NotFoundError
from baatein.domain.models import (
    Certainty,
    Fact,
    FactCategory,
    MemoryKind,
    ScoredFact,
    Sensitivity,
)
from baatein.memory.ranking import reciprocal_rank_fusion
from baatein.ports.embedder import Embedder
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.repos import (
    from_db,
    fts_query,
    is_fk_violation,
    required,
    to_db,
)

CANDIDATE_MULTIPLIER = 4
REINDEX_BATCH = 64


def vec_table_name(model_id: str, dimensions: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", model_id.lower()).strip("_")
    return f"facts_vec_{slug}_{dimensions}"


class SqliteMemoryStore:
    def __init__(self, db: Database, embedder: Embedder, min_similarity: float = 0.3) -> None:
        self._db = db
        self._embedder = embedder
        self._min_similarity = min_similarity
        self._table = vec_table_name(embedder.model_id, embedder.dimensions)
        self._index_ready = False

    @property
    def vec_table(self) -> str:
        return self._table

    async def add(self, fact: Fact) -> None:
        vector = await self._embed_passage(fact.text)
        await self._ensure_index()
        try:
            async with self._db.transaction() as conn:
                await self._insert(conn, fact, vector)
        except sqlite3.IntegrityError as exc:
            if is_fk_violation(exc):
                raise NotFoundError("user or session", fact.user_id) from exc
            raise

    async def get(self, fact_id: str) -> Fact | None:
        async with (
            self._db.read() as conn,
            conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)) as cursor,
        ):
            row = await cursor.fetchone()
        return None if row is None else _fact(row)

    async def supersede(self, fact_id: str, replacement: Fact) -> None:
        vector = await self._embed_passage(replacement.text)
        await self._ensure_index()
        async with self._db.transaction() as conn:
            pk, user_id, retired_at = await self._lookup(conn, fact_id)
            if retired_at is not None:
                raise ValueError(f"fact '{fact_id}' is already retired")
            if user_id != replacement.user_id:
                raise ValueError("a fact can only be superseded by a fact of the same user")
            await self._insert(conn, replacement, vector)
            await conn.execute(
                "UPDATE facts SET superseded_by = ?, retired_at = ? WHERE pk = ?",
                (replacement.id, to_db(replacement.created_at), pk),
            )
            await conn.execute(f"DELETE FROM {self._table} WHERE rowid = ?", (pk,))

    async def retire(self, fact_id: str, at: datetime) -> None:
        await self._ensure_index()
        async with self._db.transaction() as conn:
            pk, _, retired_at = await self._lookup(conn, fact_id)
            if retired_at is not None:
                return
            await conn.execute("UPDATE facts SET retired_at = ? WHERE pk = ?", (to_db(at), pk))
            await conn.execute(f"DELETE FROM {self._table} WHERE rowid = ?", (pk,))

    async def active(self, user_id: str, limit: int) -> list[Fact]:
        async with (
            self._db.read() as conn,
            conn.execute(
                "SELECT * FROM facts WHERE user_id = ? AND retired_at IS NULL"
                " ORDER BY importance DESC, created_at DESC LIMIT ?",
                (user_id, limit),
            ) as cursor,
        ):
            rows = await cursor.fetchall()
        return [_fact(row) for row in rows]

    async def search(self, user_id: str, query: str, limit: int) -> list[ScoredFact]:
        match = fts_query(query)
        if match is None:
            return []
        [vector] = await self._embedder.embed([query], "query")
        await self._ensure_index()
        candidates = limit * CANDIDATE_MULTIPLIER

        async with self._db.read() as conn:
            async with conn.execute(
                f"SELECT rowid, distance FROM {self._table}"
                " WHERE embedding MATCH ? AND k = ? AND user_id = ? ORDER BY distance",
                (serialize_float32(vector), candidates, user_id),
            ) as cursor:
                semantic = [
                    pk
                    for pk, distance in await cursor.fetchall()
                    if 1.0 - distance >= self._min_similarity
                ]
            async with conn.execute(
                "SELECT facts.pk FROM facts_fts JOIN facts ON facts.pk = facts_fts.rowid"
                " WHERE facts_fts MATCH ? AND facts.user_id = ? AND facts.retired_at IS NULL"
                " ORDER BY bm25(facts_fts) LIMIT ?",
                (match, user_id, candidates),
            ) as cursor:
                keyword = [pk for (pk,) in await cursor.fetchall()]

            scores = reciprocal_rank_fusion([semantic, keyword])
            if not scores:
                return []
            placeholders = ", ".join("?" * len(scores))
            async with conn.execute(
                f"SELECT * FROM facts WHERE pk IN ({placeholders})", tuple(scores)
            ) as cursor:
                rows = await cursor.fetchall()

        scored = [ScoredFact(_fact(row), scores[row["pk"]]) for row in rows]
        scored.sort(key=lambda s: (-s.score, -s.fact.importance, -s.fact.created_at.timestamp()))
        return scored[:limit]

    async def count_about(self, person_id: str) -> int:
        async with (
            self._db.read() as conn,
            conn.execute(
                "SELECT COUNT(*) FROM facts WHERE about_person_id = ? AND retired_at IS NULL",
                (person_id,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def forget(self, fact_id: str, at: datetime) -> list[str]:
        await self._ensure_index()
        async with self._db.transaction() as conn:
            await self._lookup(conn, fact_id)
            chain: dict[int, str | None] = {}
            frontier = [fact_id]
            while frontier:
                marks = ", ".join("?" * len(frontier))
                async with conn.execute(
                    "SELECT pk, id, source_session_id FROM facts"
                    f" WHERE id IN ({marks}) OR superseded_by IN ({marks})",
                    (*frontier, *frontier),
                ) as cursor:
                    rows = [row for row in await cursor.fetchall() if row["pk"] not in chain]
                chain |= {row["pk"]: row["source_session_id"] for row in rows}
                frontier = [row["id"] for row in rows]
            for pk in chain:
                await conn.execute(
                    "UPDATE facts SET text = '', quote = NULL,"
                    " deleted_at = COALESCE(deleted_at, ?), retired_at = COALESCE(retired_at, ?)"
                    " WHERE pk = ?",
                    (to_db(at), to_db(at), pk),
                )
                await conn.execute(f"DELETE FROM {self._table} WHERE rowid = ?", (pk,))
        return sorted({session_id for session_id in chain.values() if session_id})

    async def rebuild_index(self) -> int:
        async with self._db.transaction() as conn:
            await conn.execute(f"DROP TRIGGER IF EXISTS {self._table}_ad")
            await conn.execute(f"DROP TABLE IF EXISTS {self._table}")
        self._index_ready = False
        await self._ensure_index()

        async with (
            self._db.read() as conn,
            conn.execute(
                "SELECT pk, user_id, text FROM facts WHERE retired_at IS NULL ORDER BY pk"
            ) as cursor,
        ):
            rows = [(pk, user_id, text) for pk, user_id, text in await cursor.fetchall()]

        for start in range(0, len(rows), REINDEX_BATCH):
            batch = rows[start : start + REINDEX_BATCH]
            vectors = await self._embedder.embed([text for _, _, text in batch], "passage")
            async with self._db.transaction() as conn:
                await conn.executemany(
                    f"INSERT INTO {self._table} (rowid, user_id, embedding) VALUES (?, ?, ?)",
                    [
                        (pk, user_id, serialize_float32(vector))
                        for (pk, user_id, _), vector in zip(batch, vectors, strict=True)
                    ],
                )
        return len(rows)

    async def _embed_passage(self, text: str) -> list[float]:
        [vector] = await self._embedder.embed([text], "passage")
        return vector

    async def _ensure_index(self) -> None:
        if self._index_ready:
            return
        async with self._db.transaction() as conn:
            await conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._table} USING vec0("
                " user_id TEXT PARTITION KEY,"
                f" embedding FLOAT[{self._embedder.dimensions}] distance_metric=cosine)"
            )
            await conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS {self._table}_ad AFTER DELETE ON facts BEGIN"
                f" DELETE FROM {self._table} WHERE rowid = old.pk; END"
            )
        self._index_ready = True

    async def _insert(self, conn: aiosqlite.Connection, fact: Fact, vector: list[float]) -> None:
        cursor = await conn.execute(
            "INSERT INTO facts (id, user_id, text, category, importance, source_session_id,"
            " superseded_by, created_at, retired_at, kind, quote, about_person_id, certainty,"
            " sensitivity, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fact.id,
                fact.user_id,
                fact.text,
                fact.category.value,
                fact.importance,
                fact.source_session_id,
                fact.superseded_by,
                to_db(fact.created_at),
                to_db(fact.retired_at),
                fact.kind.value,
                fact.quote,
                fact.about_person_id,
                fact.certainty.value,
                fact.sensitivity.value,
                to_db(fact.deleted_at),
            ),
        )
        if fact.is_active:
            await conn.execute(
                f"INSERT INTO {self._table} (rowid, user_id, embedding) VALUES (?, ?, ?)",
                (cursor.lastrowid, fact.user_id, serialize_float32(vector)),
            )

    async def _lookup(
        self, conn: aiosqlite.Connection, fact_id: str
    ) -> tuple[int, str, str | None]:
        async with conn.execute(
            "SELECT pk, user_id, retired_at FROM facts WHERE id = ?", (fact_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("fact", fact_id)
        return row["pk"], row["user_id"], row["retired_at"]


def _fact(row: aiosqlite.Row) -> Fact:
    return Fact(
        id=row["id"],
        user_id=row["user_id"],
        text=row["text"],
        created_at=required(row["created_at"]),
        category=FactCategory(row["category"]),
        importance=row["importance"],
        source_session_id=row["source_session_id"],
        superseded_by=row["superseded_by"],
        retired_at=from_db(row["retired_at"]),
        kind=MemoryKind(row["kind"]),
        quote=row["quote"],
        about_person_id=row["about_person_id"],
        certainty=Certainty(row["certainty"]),
        sensitivity=Sensitivity(row["sensitivity"]),
        deleted_at=from_db(row["deleted_at"]),
    )
