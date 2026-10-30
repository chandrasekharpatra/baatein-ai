import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from baatein.ports.embedder import EmbeddingDimensionError
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.memory_store import SqliteMemoryStore, vec_table_name
from baatein.storage.sqlite.migrate import migrate
from baatein.storage.sqlite.repos import SqliteUserRepo
from tests.factories import at, make_fact, make_user
from tests.fakes.embedder import FakeEmbedder, MappedEmbedder


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    path = tmp_path / "app.db"
    migrate(path)
    database = Database(path)
    yield database
    await database.close()


async def add_user(db: Database) -> str:
    user = make_user()
    await SqliteUserRepo(db).add(user)
    return user.id


async def vector_rows(db: Database, table: str) -> int:
    async with db.read() as conn, conn.execute(f"SELECT count(*) FROM {table}") as cursor:
        row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


def test_vec_table_name_is_safe_and_model_specific() -> None:
    assert (
        vec_table_name("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 384)
        == "facts_vec_sentence_transformers_paraphrase_multilingual_minilm_l12_v2_384"
    )
    assert vec_table_name("a", 384) != vec_table_name("a", 768)


async def test_each_embedder_gets_its_own_index(db: Database) -> None:
    user_id = await add_user(db)
    small = SqliteMemoryStore(db, FakeEmbedder(dimensions=64, model_id="fake/small"))
    large = SqliteMemoryStore(db, FakeEmbedder(dimensions=128, model_id="fake/large"))

    await small.add(make_fact(user_id, "Loves biryani"))

    assert small.vec_table != large.vec_table
    assert await vector_rows(db, small.vec_table) == 1
    assert [r.fact.text for r in await large.search(user_id, "biryani", 5)] == ["Loves biryani"]


async def test_rebuild_index_embeds_only_active_facts(db: Database) -> None:
    user_id = await add_user(db)
    old = SqliteMemoryStore(db, FakeEmbedder(model_id="fake/old"))
    for i in range(70):
        await old.add(make_fact(user_id, f"fact number {i}"))
    retired = make_fact(user_id, "retired fact")
    await old.add(retired)
    await old.retire(retired.id, at(1))
    new_embedder = FakeEmbedder(model_id="fake/new")
    new = SqliteMemoryStore(db, new_embedder)

    count = await new.rebuild_index()
    again = await new.rebuild_index()

    assert count == again == 70
    assert await vector_rows(db, new.vec_table) == 70
    assert {kind for _, kind in new_embedder.calls} == {"passage"}
    results = await new.search(user_id, "fact number 42", 1)
    assert [r.fact.text for r in results] == ["fact number 42"]


async def test_add_embeds_as_passage_and_search_as_query(db: Database) -> None:
    user_id = await add_user(db)
    embedder = FakeEmbedder()
    store = SqliteMemoryStore(db, embedder)

    await store.add(make_fact(user_id, "Drinks chai"))
    await store.search(user_id, "chai", 5)

    assert embedder.calls == [(["Drinks chai"], "passage"), (["chai"], "query")]


async def test_inactive_facts_are_stored_without_vectors(db: Database) -> None:
    store = SqliteMemoryStore(db, FakeEmbedder())

    await store.add(make_fact(await add_user(db), "old news", retired_at=at(1)))

    assert await vector_rows(db, store.vec_table) == 0


async def test_retire_and_supersede_remove_vectors(db: Database) -> None:
    user_id = await add_user(db)
    store = SqliteMemoryStore(db, FakeEmbedder())
    a, b = make_fact(user_id, "a fact"), make_fact(user_id, "b fact")
    await store.add(a)
    await store.add(b)

    await store.retire(a.id, at(1))
    await store.supersede(b.id, make_fact(user_id, "c fact"))

    assert await vector_rows(db, store.vec_table) == 1


async def test_deleting_user_removes_facts_vectors_and_search_index(db: Database) -> None:
    user_id = await add_user(db)
    store = SqliteMemoryStore(db, FakeEmbedder())
    await store.add(make_fact(user_id, "secret hobby"))

    async with db.transaction() as conn:
        await conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    assert await vector_rows(db, store.vec_table) == 0
    assert await store.search(user_id, "secret hobby", 5) == []


async def test_keyword_match_survives_when_semantic_score_is_below_threshold(db: Database) -> None:
    user_id = await add_user(db)
    strict = SqliteMemoryStore(db, FakeEmbedder(), min_similarity=0.99)
    await strict.add(make_fact(user_id, "plays guitar in a band"))

    results = await strict.search(user_id, "guitar lessons", 5)

    assert [r.fact.text for r in results] == ["plays guitar in a band"]


async def test_embedder_dimension_mismatch_is_not_stored(db: Database) -> None:
    class WrongDims(FakeEmbedder):
        async def embed(self, texts, kind):  # type: ignore[no-untyped-def]
            raise EmbeddingDimensionError(self.model_id, self.dimensions, 3)

    store = SqliteMemoryStore(db, WrongDims())
    fact = make_fact(await add_user(db))

    with pytest.raises(EmbeddingDimensionError):
        await store.add(fact)

    assert await store.get(fact.id) is None


async def test_duplicate_fact_id_propagates_integrity_error(db: Database) -> None:
    store = SqliteMemoryStore(db, FakeEmbedder())
    fact = make_fact(await add_user(db))
    await store.add(fact)

    with pytest.raises(sqlite3.IntegrityError):
        await store.add(fact)


async def test_keyword_matches_outside_the_nearest_candidates_are_scored_too(
    db: Database,
) -> None:
    # A keyword match beyond the nearest `limit * 4` facts has no similarity yet; it is scored
    # on its own before the floor applies.
    user_id = await add_user(db)
    near = {f"near {name}": [1.0, 0.0] for name in ("alpha", "beta", "gamma", "delta")}
    embedder = MappedEmbedder(
        {"guitar": [1.0, 0.0], **near, "guitar teacher": [0.5, 0.87], "old guitar": [0.02, 1.0]}
    )
    store = SqliteMemoryStore(db, embedder)
    for text in [*near, "guitar teacher", "old guitar"]:
        await store.add(make_fact(user_id, text))

    found = await store.search(user_id, "guitar", 1, min_similarity=1.01)

    assert [r.fact.text for r in found] == ["guitar teacher"]
