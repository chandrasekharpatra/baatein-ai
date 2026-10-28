from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from baatein.domain.errors import NotFoundError
from baatein.domain.models import FactCategory
from baatein.ports.memory import MemoryStore
from baatein.ports.repos import UserRepo
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.memory_store import SqliteMemoryStore
from baatein.storage.sqlite.migrate import migrate
from baatein.storage.sqlite.repos import SqliteUserRepo
from tests.factories import at, make_fact, make_user
from tests.fakes.embedder import FakeEmbedder
from tests.fakes.memory import InMemoryMemoryStore
from tests.fakes.repos import InMemoryState, InMemoryUserRepo


@dataclass(frozen=True)
class Memory:
    store: MemoryStore
    users: UserRepo


@pytest.fixture(params=["sqlite", "memory"])
async def memory(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Memory]:
    if request.param == "memory":
        state = InMemoryState()
        yield Memory(InMemoryMemoryStore(state, FakeEmbedder()), InMemoryUserRepo(state))
        return

    path = tmp_path / "test.db"
    migrate(path)
    db = Database(path)
    yield Memory(SqliteMemoryStore(db, FakeEmbedder()), SqliteUserRepo(db))
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
