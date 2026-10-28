import pytest

from baatein.domain.errors import DuplicateTurnError, NotFoundError
from baatein.domain.models import Role
from tests.contract.conftest import Repos
from tests.factories import make_session, make_turn, make_user


async def add_session(repos: Repos, user_id: str | None = None) -> str:
    if user_id is None:
        user = make_user()
        await repos.users.add(user)
        user_id = user.id
    session = make_session(user_id)
    await repos.sessions.add(session)
    return session.id


async def test_append_assigns_id_and_round_trips(repos: Repos) -> None:
    session_id = await add_session(repos)
    turn = make_turn(session_id, 1, "Namaste", role=Role.ASSISTANT, language="hi", interrupted=True)

    stored = await repos.turns.append(turn)

    assert stored.id is not None
    assert await repos.turns.list_for_session(session_id) == [stored]
    assert stored.text == "Namaste"
    assert stored.role is Role.ASSISTANT
    assert stored.interrupted is True


async def test_append_to_unknown_session_raises(repos: Repos) -> None:
    with pytest.raises(NotFoundError):
        await repos.turns.append(make_turn("missing", 1))


async def test_duplicate_seq_rejected(repos: Repos) -> None:
    session_id = await add_session(repos)
    await repos.turns.append(make_turn(session_id, 1))

    with pytest.raises(DuplicateTurnError):
        await repos.turns.append(make_turn(session_id, 1))


async def test_list_for_session_ordered_by_seq_after_cursor(repos: Repos) -> None:
    session_id = await add_session(repos)
    other_id = await add_session(repos)
    for seq in (3, 1, 2):
        await repos.turns.append(make_turn(session_id, seq))
    await repos.turns.append(make_turn(other_id, 4))

    assert [t.seq for t in await repos.turns.list_for_session(session_id)] == [1, 2, 3]
    assert [t.seq for t in await repos.turns.list_for_session(session_id, after_seq=1)] == [2, 3]


async def test_search_matches_any_term_scoped_to_user(repos: Repos) -> None:
    user = make_user()
    other = make_user()
    await repos.users.add(user)
    await repos.users.add(other)
    session_id = await add_session(repos, user.id)
    other_session = await add_session(repos, other.id)
    interview = await repos.turns.append(make_turn(session_id, 1, "Friday ko mera interview hai"))
    await repos.turns.append(make_turn(session_id, 2, "Aaj mausam accha hai"))
    await repos.turns.append(make_turn(other_session, 1, "My interview went well"))

    results = await repos.turns.search(user.id, "interview kab hai?", limit=5)

    assert interview.id in {t.id for t in results}
    assert all(t.session_id == session_id for t in results)


async def test_search_ranks_better_matches_first_and_limits(repos: Repos) -> None:
    session_id = await add_session(repos)
    user_id = (await repos.sessions.get(session_id)).user_id  # type: ignore[union-attr]
    weak = await repos.turns.append(make_turn(session_id, 1, "wedding plans"))
    strong = await repos.turns.append(make_turn(session_id, 2, "sister wedding in December"))

    results = await repos.turns.search(user_id, "sister wedding December", limit=1)

    assert [t.id for t in results] == [strong.id]
    assert weak.id != strong.id


@pytest.mark.parametrize("query", ["", "   ", '"" ?? !!'])
async def test_search_with_no_terms_returns_nothing(repos: Repos, query: str) -> None:
    session_id = await add_session(repos)
    user_id = (await repos.sessions.get(session_id)).user_id  # type: ignore[union-attr]
    await repos.turns.append(make_turn(session_id, 1, "hello"))

    assert await repos.turns.search(user_id, query, limit=5) == []
