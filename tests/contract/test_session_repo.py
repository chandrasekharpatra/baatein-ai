import pytest

from baatein.domain.errors import NotFoundError
from baatein.domain.models import CallSummary, FinalizeStatus, Session
from tests.contract.conftest import Repos
from tests.factories import at, make_session, make_user


async def add_user(repos: Repos) -> str:
    user = make_user()
    await repos.users.add(user)
    return user.id


async def test_add_then_get_round_trips(repos: Repos) -> None:
    session = make_session(
        await add_user(repos),
        room_name="room-1",
        ended_abruptly=True,
        unfinished_topic="mela",
        notes=("Mentioned a trip to Puri",),
        saving_paused=True,
        finalize_retry_at=at(9),
    )

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
    assert (ended.ended_abruptly, ended.unfinished_topic) == (False, None)
    assert ended.saving_paused is False


async def test_end_records_that_saving_was_paused(repos: Repos) -> None:
    session = make_session(await add_user(repos))
    await repos.sessions.add(session)

    await repos.sessions.end(session.id, at(5), None, saving_paused=True)

    ended = await repos.sessions.get(session.id)
    assert ended is not None
    assert ended.saving_paused is True


async def test_end_records_a_call_cut_off_mid_story(repos: Repos) -> None:
    session = make_session(await add_user(repos))
    await repos.sessions.add(session)

    await repos.sessions.end(
        session.id, at(5), None, ended_abruptly=True, unfinished_topic="Gaon ka mela"
    )

    ended = await repos.sessions.get(session.id)
    assert ended is not None
    assert (ended.ended_abruptly, ended.unfinished_topic) == (True, "Gaon ka mela")


async def test_finalize_lifecycle(repos: Repos) -> None:
    session = make_session(await add_user(repos))
    await repos.sessions.add(session)
    await repos.sessions.end(
        session.id, at(5), None, ended_abruptly=True, unfinished_topic="Kal main bata raha tha"
    )

    await repos.sessions.mark_finalize_failed(session.id, at(6))
    await repos.sessions.mark_finalize_failed(session.id, at(8))
    failed = await repos.sessions.get(session.id)
    await repos.sessions.mark_finalized(
        session.id,
        CallSummary(
            "Started a childhood story; the call ended before it finished.",
            ended_abruptly=True,
            unfinished_topic="childhood story about the village",
            notes=("Mentioned his old school friend Bijay",),
        ),
    )
    done = await repos.sessions.get(session.id)

    assert failed is not None
    assert (failed.finalize_status, failed.finalize_attempts) == (FinalizeStatus.FAILED, 2)
    assert failed.finalize_retry_at == at(8)
    assert done is not None
    assert done.finalize_status is FinalizeStatus.DONE
    assert done.summary == "Started a childhood story; the call ended before it finished."
    assert (done.ended_abruptly, done.unfinished_topic) == (
        True,
        "childhood story about the village",
    )
    assert done.notes == ("Mentioned his old school friend Bijay",)
    assert done.finalize_retry_at is None


async def test_finalized_verdict_replaces_the_guess_from_end(repos: Repos) -> None:
    session = make_session(await add_user(repos))
    await repos.sessions.add(session)
    await repos.sessions.end(session.id, at(5), None, ended_abruptly=True, unfinished_topic="x")

    await repos.sessions.mark_finalized(session.id, CallSummary("Said goodbye."))

    done = await repos.sessions.get(session.id)
    assert done is not None
    assert (done.ended_abruptly, done.unfinished_topic, done.notes) == (False, None, ())


async def test_pending_finalization_filters_orders_and_limits(repos: Repos) -> None:
    user_id = await add_user(repos)

    async def ended(minute: int) -> Session:
        session = make_session(user_id)
        await repos.sessions.add(session)
        await repos.sessions.end(session.id, at(minute), None)
        return session

    active = make_session(user_id)
    await repos.sessions.add(active)
    late, early, retrying, waiting, exhausted, done = (
        await ended(30),
        await ended(10),
        await ended(20),
        await ended(25),
        await ended(5),
        await ended(1),
    )
    await repos.sessions.mark_finalize_failed(retrying.id, at(40))
    await repos.sessions.mark_finalize_failed(waiting.id, at(60))
    for _ in range(3):
        await repos.sessions.mark_finalize_failed(exhausted.id, at(40))
    await repos.sessions.mark_finalized(done.id, CallSummary(None))

    pending = await repos.sessions.pending_finalization(max_attempts=3, limit=10, now=at(45))
    limited = await repos.sessions.pending_finalization(max_attempts=3, limit=1, now=at(45))
    later = await repos.sessions.pending_finalization(max_attempts=3, limit=10, now=at(60))

    assert [s.id for s in pending] == [early.id, retrying.id, late.id]
    assert [s.id for s in limited] == [early.id]
    assert waiting.id in {s.id for s in later}


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
        lambda r: r.sessions.mark_finalized("missing", CallSummary(None)),
        lambda r: r.sessions.mark_finalize_failed("missing", at(0)),
    ],
    ids=["end", "set_memory_synced_seq", "mark_finalized", "mark_finalize_failed"],
)
async def test_updates_on_missing_session_raise(repos: Repos, action) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(NotFoundError):
        await action(repos)


async def test_clear_summary(repos: Repos) -> None:
    user = make_user()
    await repos.users.add(user)
    session = make_session(
        user.id, summary="Talked about Ramesh", unfinished_topic="Ramesh", notes=("Ramesh",)
    )
    await repos.sessions.add(session)

    await repos.sessions.clear_summary(session.id)

    stored = await repos.sessions.get(session.id)
    assert stored is not None
    assert (stored.summary, stored.unfinished_topic, stored.notes) == (None, None, ())
    with pytest.raises(NotFoundError):
        await repos.sessions.clear_summary("missing")
