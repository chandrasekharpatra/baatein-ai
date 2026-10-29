import pytest

from baatein.domain.errors import NotFoundError
from baatein.domain.models import FinalizeStatus, Session
from tests.contract.conftest import Repos
from tests.factories import at, make_session, make_user


async def add_user(repos: Repos) -> str:
    user = make_user()
    await repos.users.add(user)
    return user.id


async def test_add_then_get_round_trips(repos: Repos) -> None:
    session = make_session(await add_user(repos), room_name="room-1")

    await repos.sessions.add(session)

    assert await repos.sessions.get(session.id) == session
    assert await repos.sessions.get("missing") is None


async def test_add_for_unknown_user_raises(repos: Repos) -> None:
    with pytest.raises(NotFoundError):
        await repos.sessions.add(make_session("missing-user"))


async def test_recent_for_user_newest_first_limited_and_scoped(repos: Repos) -> None:
    user_id, other_id = await add_user(repos), await add_user(repos)
    old, new, newest = (make_session(user_id, started_at=at(m)) for m in (0, 10, 20))
    for session in (new, old, newest, make_session(other_id, started_at=at(30))):
        await repos.sessions.add(session)

    recent = await repos.sessions.recent_for_user(user_id, limit=2)

    assert [s.id for s in recent] == [newest.id, new.id]


async def test_end_marks_pending_finalization(repos: Repos) -> None:
    session = make_session(await add_user(repos))
    await repos.sessions.add(session)

    await repos.sessions.end(session.id, at(5), "participant_disconnected")

    ended = await repos.sessions.get(session.id)
    assert ended is not None
    assert ended.ended_at == at(5)
    assert ended.end_reason == "participant_disconnected"
    assert ended.finalize_status is FinalizeStatus.PENDING


async def test_finalize_lifecycle(repos: Repos) -> None:
    session = make_session(await add_user(repos))
    await repos.sessions.add(session)
    await repos.sessions.end(session.id, at(5), None)

    await repos.sessions.mark_finalize_failed(session.id)
    await repos.sessions.mark_finalize_failed(session.id)
    failed = await repos.sessions.get(session.id)
    await repos.sessions.mark_finalized(session.id, "Talked about the interview.")
    done = await repos.sessions.get(session.id)

    assert failed is not None
    assert (failed.finalize_status, failed.finalize_attempts) == (FinalizeStatus.FAILED, 2)
    assert done is not None
    assert done.finalize_status is FinalizeStatus.DONE
    assert done.summary == "Talked about the interview."


async def test_pending_finalization_filters_orders_and_limits(repos: Repos) -> None:
    user_id = await add_user(repos)

    async def ended(minute: int) -> Session:
        session = make_session(user_id)
        await repos.sessions.add(session)
        await repos.sessions.end(session.id, at(minute), None)
        return session

    active = make_session(user_id)
    await repos.sessions.add(active)
    late, early, retrying, exhausted, done = (
        await ended(30),
        await ended(10),
        await ended(20),
        await ended(5),
        await ended(1),
    )
    await repos.sessions.mark_finalize_failed(retrying.id)
    for _ in range(3):
        await repos.sessions.mark_finalize_failed(exhausted.id)
    await repos.sessions.mark_finalized(done.id, None)

    pending = await repos.sessions.pending_finalization(max_attempts=3, limit=10)
    limited = await repos.sessions.pending_finalization(max_attempts=3, limit=1)

    assert [s.id for s in pending] == [early.id, retrying.id, late.id]
    assert [s.id for s in limited] == [early.id]


async def test_set_memory_synced_seq(repos: Repos) -> None:
    session = make_session(await add_user(repos))
    await repos.sessions.add(session)

    await repos.sessions.set_memory_synced_seq(session.id, 7)

    synced = await repos.sessions.get(session.id)
    assert synced is not None
    assert synced.memory_synced_seq == 7


@pytest.mark.parametrize(
    "action",
    [
        lambda r: r.sessions.end("missing", at(0), None),
        lambda r: r.sessions.set_memory_synced_seq("missing", 1),
        lambda r: r.sessions.mark_finalized("missing", None),
        lambda r: r.sessions.mark_finalize_failed("missing"),
    ],
    ids=["end", "set_memory_synced_seq", "mark_finalized", "mark_finalize_failed"],
)
async def test_updates_on_missing_session_raise(repos: Repos, action) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(NotFoundError):
        await action(repos)


async def test_clear_summary(repos: Repos) -> None:
    user = make_user()
    await repos.users.add(user)
    session = make_session(user.id, summary="Talked about Ramesh")
    await repos.sessions.add(session)

    await repos.sessions.clear_summary(session.id)

    stored = await repos.sessions.get(session.id)
    assert stored is not None
    assert stored.summary is None
    with pytest.raises(NotFoundError):
        await repos.sessions.clear_summary("missing")
