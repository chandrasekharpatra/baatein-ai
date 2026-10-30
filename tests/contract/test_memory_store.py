from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from baatein.domain.errors import NotFoundError
from baatein.domain.models import (
    Certainty,
    FactCategory,
    MemoryKind,
    Person,
    Sensitivity,
)
from baatein.ports.memory import MemoryStore
from baatein.ports.repos import PeopleRepo, SessionRepo, UserRepo
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.memory_store import SqliteMemoryStore
from baatein.storage.sqlite.migrate import migrate
from baatein.storage.sqlite.repos import SqlitePeopleRepo, SqliteSessionRepo, SqliteUserRepo
from tests.factories import T0, at, make_fact, make_session, make_user
from tests.fakes.embedder import FakeEmbedder, MappedEmbedder
from tests.fakes.memory import InMemoryMemoryStore
from tests.fakes.repos import (
    InMemoryPeopleRepo,
    InMemorySessionRepo,
    InMemoryState,
    InMemoryUserRepo,
)


@dataclass(frozen=True)
class Memory:
    store: MemoryStore
    users: UserRepo
    sessions: SessionRepo
    people: PeopleRepo


@pytest.fixture(params=["sqlite", "memory"])
async def memory(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Memory]:
    if request.param == "memory":
        state = InMemoryState()
        yield Memory(
            InMemoryMemoryStore(state, FakeEmbedder()),
            InMemoryUserRepo(state),
            InMemorySessionRepo(state),
            InMemoryPeopleRepo(state),
        )
        return

    path = tmp_path / "test.db"
    migrate(path)
    db = Database(path)
    yield Memory(
        SqliteMemoryStore(db, FakeEmbedder()),
        SqliteUserRepo(db),
        SqliteSessionRepo(db),
        SqlitePeopleRepo(db),
    )
    await db.close()


async def add_user(memory: Memory) -> str:
    user = make_user()
    await memory.users.add(user)
    return user.id


async def test_add_then_get_round_trips(memory: Memory) -> None:
    fact = make_fact(
        await add_user(memory),
        "Sister Priya is getting married in December",
        category=FactCategory.RELATIONSHIP,
        importance=5,
    )

    await memory.store.add(fact)

    assert await memory.store.get(fact.id) == fact
    assert await memory.store.get("missing") is None


async def test_add_for_unknown_user_raises(memory: Memory) -> None:
    with pytest.raises(NotFoundError):
        await memory.store.add(make_fact("missing-user"))


async def test_active_orders_by_importance_then_recency_and_limits(memory: Memory) -> None:
    user_id = await add_user(memory)
    other_id = await add_user(memory)
    minor = make_fact(user_id, importance=1)
    old_major = make_fact(user_id, importance=5, created_at=at(0))
    new_major = make_fact(user_id, importance=5, created_at=at(10))
    for fact in (minor, old_major, new_major, make_fact(other_id, importance=5)):
        await memory.store.add(fact)

    active = await memory.store.active(user_id, limit=10)

    assert [f.id for f in active] == [new_major.id, old_major.id, minor.id]
    assert len(await memory.store.active(user_id, limit=2)) == 2


async def test_search_finds_related_facts_scoped_to_user(memory: Memory) -> None:
    user_id = await add_user(memory)
    other_id = await add_user(memory)
    wedding = make_fact(user_id, "Sister Priya is getting married in December")
    await memory.store.add(wedding)
    await memory.store.add(make_fact(user_id, "Works as a nurse in Pune"))
    await memory.store.add(make_fact(other_id, "Sister is getting married in December"))

    results = await memory.store.search(user_id, "when is my sister's wedding in December", 5)

    assert [r.fact.id for r in results] == [wedding.id]
    assert results[0].score > 0


async def test_search_ranks_stronger_matches_first_and_limits(memory: Memory) -> None:
    user_id = await add_user(memory)
    weak = make_fact(user_id, "Enjoys cricket on weekends")
    strong = make_fact(user_id, "Plays cricket with college friends every weekend")
    await memory.store.add(weak)
    await memory.store.add(strong)

    results = await memory.store.search(user_id, "cricket with college friends", 1)

    assert [r.fact.id for r in results] == [strong.id]


async def test_search_ignores_unrelated_facts(memory: Memory) -> None:
    user_id = await add_user(memory)
    await memory.store.add(make_fact(user_id, "Allergic to peanuts"))

    assert await memory.store.search(user_id, "favourite movie", 5) == []


@pytest.mark.parametrize("query", ["", "  ", "?? !!"])
async def test_search_with_no_terms_returns_nothing(memory: Memory, query: str) -> None:
    user_id = await add_user(memory)
    await memory.store.add(make_fact(user_id))

    assert await memory.store.search(user_id, query, 5) == []


async def test_supersede_retires_old_and_adds_replacement(memory: Memory) -> None:
    user_id = await add_user(memory)
    old = make_fact(user_id, "Lives in Pune")
    new = make_fact(user_id, "Moved to Bangalore for work", created_at=at(30))
    await memory.store.add(old)

    await memory.store.supersede(old.id, new)

    retired = await memory.store.get(old.id)
    assert retired is not None
    assert (retired.superseded_by, retired.retired_at, retired.is_active) == (new.id, at(30), False)
    assert [f.id for f in await memory.store.active(user_id, 10)] == [new.id]
    assert [r.fact.id for r in await memory.store.search(user_id, "lives in Pune", 5)] == []
    assert [r.fact.id for r in await memory.store.search(user_id, "Bangalore", 5)] == [new.id]


async def test_supersede_rejects_missing_retired_or_foreign_facts(memory: Memory) -> None:
    user_id = await add_user(memory)
    other_id = await add_user(memory)
    fact = make_fact(user_id)
    await memory.store.add(fact)
    await memory.store.retire(fact.id, at(1))
    live = make_fact(user_id)
    await memory.store.add(live)

    with pytest.raises(NotFoundError):
        await memory.store.supersede("missing", make_fact(user_id))
    with pytest.raises(ValueError, match="already retired"):
        await memory.store.supersede(fact.id, make_fact(user_id))
    with pytest.raises(ValueError, match="same user"):
        await memory.store.supersede(live.id, make_fact(other_id))


async def test_retire_hides_fact_and_is_idempotent(memory: Memory) -> None:
    user_id = await add_user(memory)
    fact = make_fact(user_id, "Training for a marathon")
    await memory.store.add(fact)

    await memory.store.retire(fact.id, at(5))
    await memory.store.retire(fact.id, at(9))

    retired = await memory.store.get(fact.id)
    assert retired is not None
    assert retired.retired_at == at(5)
    assert await memory.store.active(user_id, 10) == []
    assert await memory.store.search(user_id, "marathon training", 5) == []


async def test_retire_missing_raises(memory: Memory) -> None:
    with pytest.raises(NotFoundError):
        await memory.store.retire("missing", at(0))


async def add_session(memory: Memory, user_id: str) -> str:
    session = make_session(user_id)
    await memory.sessions.add(session)
    return session.id


async def test_memory_fields_round_trip(memory: Memory) -> None:
    user_id = await add_user(memory)
    ramesh = Person("p-ramesh", user_id, "Ramesh", T0, relation="college friend")
    await memory.people.add(ramesh)
    fact = make_fact(
        user_id,
        "Misses college friend Ramesh",
        kind=MemoryKind.STORY,
        quote="Hum har shaam saath chai peete the",
        about_person_id=ramesh.id,
        certainty=Certainty.UNCERTAIN,
        sensitivity=Sensitivity.PERSONAL,
        source_session_id=await add_session(memory, user_id),
    )

    await memory.store.add(fact)

    assert await memory.store.get(fact.id) == fact


async def test_forget_tombstones_fact_and_earlier_versions(memory: Memory) -> None:
    user_id = await add_user(memory)
    first, second = await add_session(memory, user_id), await add_session(memory, user_id)
    original = make_fact(user_id, "Suman is my sister", quote="Suman", source_session_id=first)
    await memory.store.add(original)
    corrected = make_fact(
        user_id, "Sunita is my neighbour", created_at=at(5), source_session_id=second
    )
    await memory.store.supersede(original.id, corrected)
    unrelated = make_fact(user_id, "Likes masala chai")
    await memory.store.add(unrelated)

    sessions = await memory.store.forget(corrected.id, at(10))

    assert sessions == sorted([first, second])
    for fact_id in (original.id, corrected.id):
        tombstone = await memory.store.get(fact_id)
        assert tombstone is not None
        assert (tombstone.text, tombstone.quote) == ("", None)
        assert tombstone.is_deleted
        assert not tombstone.is_active
    assert (await memory.store.get(corrected.id)).deleted_at == at(10)  # type: ignore[union-attr]
    assert await memory.store.active(user_id, limit=10) == [unrelated]
    assert await memory.store.search(user_id, "Sunita neighbour Suman sister", limit=5) == []


async def test_forget_is_idempotent_and_keeps_first_deletion_time(memory: Memory) -> None:
    user_id = await add_user(memory)
    fact = make_fact(user_id, "Something private")
    await memory.store.add(fact)
    await memory.store.forget(fact.id, at(1))

    assert await memory.store.forget(fact.id, at(2)) == []
    assert (await memory.store.get(fact.id)).deleted_at == at(1)  # type: ignore[union-attr]


async def test_forget_missing_raises(memory: Memory) -> None:
    with pytest.raises(NotFoundError):
        await memory.store.forget("missing", at(0))


async def test_count_about_counts_current_facts_only(memory: Memory) -> None:
    user_id = await add_user(memory)
    await memory.people.add(Person("p1", user_id, "Ravi", T0))
    current = make_fact(user_id, "Ravi plays cricket", about_person_id="p1")
    retired = make_fact(user_id, "Ravi lives in Pune", about_person_id="p1")
    for fact in (current, retired, make_fact(user_id, "Likes chai")):
        await memory.store.add(fact)
    await memory.store.retire(retired.id, at(1))

    assert await memory.store.count_about("p1") == 1
    await memory.store.forget(current.id, at(2))
    assert await memory.store.count_about("p1") == 0


async def test_saved_in_lists_current_facts_from_a_call(memory: Memory) -> None:
    user_id = await add_user(memory)
    call, other_call = make_session(user_id), make_session(user_id)
    for session in (call, other_call):
        await memory.sessions.add(session)
    second = make_fact(user_id, "Likes chai", source_session_id=call.id, created_at=at(2))
    first = make_fact(user_id, "Ravi plays cricket", source_session_id=call.id, created_at=at(1))
    forgotten = make_fact(user_id, "Ravi lives in Pune", source_session_id=call.id)
    elsewhere = make_fact(user_id, "Has a dog", source_session_id=other_call.id)
    for fact in (second, first, forgotten, elsewhere):
        await memory.store.add(fact)
    await memory.store.forget(forgotten.id, at(3))

    saved = await memory.store.saved_in(call.id)

    assert [f.id for f in saved] == [first.id, second.id]


async def test_each_search_can_set_its_own_cut_off(memory: Memory) -> None:
    # The cut-off depends on the language of the words (docs/RETRIEVAL_BENCHMARK.md).
    user_id = await add_user(memory)
    fact = make_fact(user_id, "It is in the garden")
    await memory.store.add(fact)
    # Only stopwords overlap: a meaning match without any keyword match (similarity 0.8).
    query = "is it in the zebra"

    loose = await memory.store.search(user_id, query, 5, min_similarity=0.5)
    strict = await memory.store.search(user_id, query, 5, min_similarity=0.9)

    assert [r.fact.id for r in loose] == [fact.id]
    assert strict == []


@pytest.mark.parametrize("kind", ["sqlite", "memory"])
async def test_a_keyword_match_needs_a_little_shared_meaning(kind: str, tmp_path: Path) -> None:
    # Shared words alone pulled up unrelated memories ("sleep", "pine cone"); keep a keyword
    # match only if its similarity reaches KEYWORD_FLOOR (0.1).
    embedder = MappedEmbedder(
        {
            "guitar": [1.0, 0.0],
            "plays guitar in a band": [0.05, 1.0],  # similarity ~0.05
            "gave away the old guitar": [0.2, 1.0],  # similarity ~0.2
        }
    )
    if kind == "memory":
        state = InMemoryState()
        store: MemoryStore = InMemoryMemoryStore(state, embedder)
        users: UserRepo = InMemoryUserRepo(state)
    else:
        migrate(tmp_path / "test.db")
        db = Database(tmp_path / "test.db")
        store, users = SqliteMemoryStore(db, embedder), SqliteUserRepo(db)
    user = make_user()
    await users.add(user)
    for text in ("plays guitar in a band", "gave away the old guitar"):
        await store.add(make_fact(user.id, text))

    found = await store.search(user.id, "guitar", 5, min_similarity=0.9)  # no meaning match

    assert [r.fact.text for r in found] == ["gave away the old guitar"]
    if kind == "sqlite":
        await db.close()
