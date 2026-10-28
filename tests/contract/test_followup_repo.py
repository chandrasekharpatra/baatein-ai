import pytest

from baatein.domain.errors import NotFoundError
from baatein.domain.models import FollowUpStatus
from tests.contract.conftest import Repos
from tests.factories import at, make_followup, make_session, make_user


async def add_user(repos: Repos) -> str:
    user = make_user()
    await repos.users.add(user)
    return user.id


async def test_add_then_get_round_trips(repos: Repos) -> None:
    user_id = await add_user(repos)
    session = make_session(user_id)
    await repos.sessions.add(session)
    followup = make_followup(
        user_id, text="Ask about interview", due_at=at(60), priority=2, source_session_id=session.id
    )

    await repos.followups.add(followup)

    assert await repos.followups.get(followup.id) == followup
    assert await repos.followups.get("missing") is None


async def test_add_for_unknown_user_raises(repos: Repos) -> None:
    with pytest.raises(NotFoundError):
        await repos.followups.add(make_followup("missing-user"))


async def test_list_open_orders_by_due_then_priority_then_age(repos: Repos) -> None:
    user_id = await add_user(repos)
    other_id = await add_user(repos)
    undated_old = make_followup(user_id, created_at=at(0))
    undated_new = make_followup(user_id, created_at=at(1))
    undated_urgent = make_followup(user_id, priority=5, created_at=at(2))
    due_later = make_followup(user_id, due_at=at(120))
    due_soon = make_followup(user_id, due_at=at(60))
    resolved = make_followup(user_id, due_at=at(1))
    for f in (undated_new, due_later, undated_old, resolved, undated_urgent, due_soon):
        await repos.followups.add(f)
    await repos.followups.add(make_followup(other_id, due_at=at(0)))
    await repos.followups.resolve(resolved.id, FollowUpStatus.DONE, at(5))

    open_ids = [f.id for f in await repos.followups.list_open(user_id, limit=10)]

    assert open_ids == [
        due_soon.id,
        due_later.id,
        undated_urgent.id,
        undated_old.id,
        undated_new.id,
    ]
    assert len(await repos.followups.list_open(user_id, limit=2)) == 2


async def test_resolve_sets_status_and_time(repos: Repos) -> None:
    followup = make_followup(await add_user(repos))
    await repos.followups.add(followup)

    await repos.followups.resolve(followup.id, FollowUpStatus.DROPPED, at(9))

    resolved = await repos.followups.get(followup.id)
    assert resolved is not None
    assert (resolved.status, resolved.resolved_at) == (FollowUpStatus.DROPPED, at(9))


async def test_resolve_missing_raises(repos: Repos) -> None:
    with pytest.raises(NotFoundError):
        await repos.followups.resolve("missing", FollowUpStatus.DONE, at(0))


async def test_resolve_as_open_is_rejected(repos: Repos) -> None:
    followup = make_followup(await add_user(repos))
    await repos.followups.add(followup)

    with pytest.raises(ValueError, match="done or dropped"):
        await repos.followups.resolve(followup.id, FollowUpStatus.OPEN, at(0))
