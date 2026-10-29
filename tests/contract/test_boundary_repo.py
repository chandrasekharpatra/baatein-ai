import pytest

from baatein.domain.errors import NotFoundError
from baatein.domain.models import BoundaryScope, DeclinedSuggestion
from tests.contract.conftest import Repos
from tests.factories import at, make_boundary, make_session, make_thread, make_user


async def add_user(repos: Repos) -> str:
    user = make_user()
    await repos.users.add(user)
    return user.id


async def test_add_then_get_round_trips(repos: Repos) -> None:
    user_id = await add_user(repos)
    session = make_session(user_id)
    await repos.sessions.add(session)
    thread = make_thread(user_id)
    await repos.threads.add(thread)
    boundary = make_boundary(
        user_id,
        scope=BoundaryScope.UNTIL_DATE,
        session_id=session.id,
        until=at(60),
        thread_id=thread.id,
    )

    await repos.boundaries.add(boundary)

    assert await repos.boundaries.get(boundary.id) == boundary
    assert await repos.boundaries.get("missing") is None


async def test_add_for_unknown_user_raises(repos: Repos) -> None:
    with pytest.raises(NotFoundError):
        await repos.boundaries.add(make_boundary("ghost"))


async def test_list_active_follows_each_scope(repos: Repos) -> None:
    user_id, other_id = await add_user(repos), await add_user(repos)
    this_call, last_call = make_session(user_id), make_session(user_id)
    for session in (this_call, last_call):
        await repos.sessions.add(session)
    kept = [
        make_boundary(user_id, "painting", created_at=at(1)),
        make_boundary(
            user_id,
            "today",
            scope=BoundaryScope.THIS_CALL,
            created_at=at(2),
            session_id=this_call.id,
        ),
        make_boundary(
            user_id, "almari", created_at=at(3), scope=BoundaryScope.UNTIL_DATE, until=at(11)
        ),
    ]
    dropped = [
        make_boundary(user_id, "old", scope=BoundaryScope.THIS_CALL, session_id=last_call.id),
        make_boundary(user_id, "past", scope=BoundaryScope.UNTIL_DATE, until=at(10)),
        make_boundary(user_id, "lifted", lifted_at=at(5)),
        make_boundary(other_id, "other"),
    ]
    for boundary in dropped + kept:
        await repos.boundaries.add(boundary)

    active = await repos.boundaries.list_active(user_id, this_call.id, now=at(10))

    assert [b.topic for b in active] == ["painting", "today", "almari"]


async def test_lift_keeps_the_first_lift_time(repos: Repos) -> None:
    boundary = make_boundary(await add_user(repos))
    await repos.boundaries.add(boundary)

    await repos.boundaries.lift(boundary.id, at(5))
    await repos.boundaries.lift(boundary.id, at(9))

    lifted = await repos.boundaries.get(boundary.id)
    assert lifted is not None
    assert lifted.lifted_at == at(5)
    with pytest.raises(NotFoundError):
        await repos.boundaries.lift("missing", at(0))


async def test_declined_suggestions_newest_first_limited_and_scoped(repos: Repos) -> None:
    user_id, other_id = await add_user(repos), await add_user(repos)
    session = make_session(user_id)
    await repos.sessions.add(session)
    for minute, text in ((1, "go for a walk"), (3, "join yoga"), (2, "call your son")):
        await repos.suggestions.add(
            DeclinedSuggestion(f"s{minute}", user_id, text, at(minute), session.id)
        )
    await repos.suggestions.add(DeclinedSuggestion("x", other_id, "other", at(9)))

    found = await repos.suggestions.list_for_user(user_id, limit=2)

    assert found == [
        DeclinedSuggestion("s3", user_id, "join yoga", at(3), session.id),
        DeclinedSuggestion("s2", user_id, "call your son", at(2), session.id),
    ]
    with pytest.raises(NotFoundError):
        await repos.suggestions.add(DeclinedSuggestion("y", "ghost", "x", at(0)))
