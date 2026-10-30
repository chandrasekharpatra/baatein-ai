import pytest

from baatein.domain.errors import DuplicatePersonError, NotFoundError
from baatein.domain.models import Person
from tests.contract.conftest import Repos
from tests.factories import T0, at, make_user


async def add_user(repos: Repos) -> str:
    user = make_user()
    await repos.users.add(user)
    return user.id


async def test_add_then_get_and_find_by_name_case_insensitively(repos: Repos) -> None:
    user_id = await add_user(repos)
    ravi = Person("p1", user_id, "Ravi", T0, relation="son")

    await repos.people.add(ravi)

    assert await repos.people.get("p1") == ravi
    assert await repos.people.get("missing") is None
    assert await repos.people.find_by_name(user_id, " ravi ") == ravi
    assert await repos.people.find_by_name(user_id, "Neha") is None
    assert await repos.people.find_by_name(await add_user(repos), "Ravi") is None


async def test_names_are_unique_per_user(repos: Repos) -> None:
    user_id, other_id = await add_user(repos), await add_user(repos)
    await repos.people.add(Person("p1", user_id, "Ravi", T0))

    with pytest.raises(DuplicatePersonError):
        await repos.people.add(Person("p2", user_id, "RAVI", T0))
    await repos.people.add(Person("p3", other_id, "Ravi", T0))


async def test_add_for_unknown_user_raises(repos: Repos) -> None:
    with pytest.raises(NotFoundError):
        await repos.people.add(Person("p1", "ghost", "Ravi", T0))


async def test_list_for_user_oldest_first_limited_and_scoped(repos: Repos) -> None:
    user_id, other_id = await add_user(repos), await add_user(repos)
    for minutes, name in ((2, "Neha"), (1, "Aman"), (3, "Ravi")):
        await repos.people.add(Person(f"p-{name}", user_id, name, at(minutes)))
    await repos.people.add(Person("p-x", other_id, "Other", at(0)))

    people = await repos.people.list_for_user(user_id, limit=2)

    assert [p.name for p in people] == ["Aman", "Neha"]


async def test_update_renames_and_sets_relation(repos: Repos) -> None:
    user_id = await add_user(repos)
    await repos.people.add(Person("p1", user_id, "Suman", T0))
    await repos.people.add(Person("p2", user_id, "Ravi", T0))

    await repos.people.update(Person("p1", user_id, "Sunita", T0, relation="neighbour"))

    assert await repos.people.get("p1") == Person("p1", user_id, "Sunita", T0, "neighbour")
    with pytest.raises(DuplicatePersonError):
        await repos.people.update(Person("p1", user_id, "Ravi", T0))
    with pytest.raises(NotFoundError):
        await repos.people.update(Person("missing", user_id, "X", T0))


async def test_delete_removes_person_and_is_idempotent(repos: Repos) -> None:
    user_id = await add_user(repos)
    await repos.people.add(Person("p1", user_id, "Ramesh", T0))

    await repos.people.delete("p1")
    await repos.people.delete("p1")

    assert await repos.people.get("p1") is None
