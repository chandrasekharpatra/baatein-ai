import pytest

from baatein.domain.errors import NotFoundError
from baatein.domain.models import Person, Sensitivity, ThreadEvent, ThreadKind, ThreadStatus
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
    thread = make_thread(
        user_id,
        kind=ThreadKind.RELATIONSHIP,
        status=ThreadStatus.IN_PROGRESS,
        next_step="Call him on Sunday",
        check_in_after=at(60),
        last_offered_at=at(5),
        times_offered=2,
        sensitivity=Sensitivity.PERSONAL,
        source_session_id=session.id,
    )

    await repos.threads.add(thread)

    assert await repos.threads.get(thread.id) == thread
    assert await repos.threads.get("missing") is None


async def test_add_for_unknown_user_raises(repos: Repos) -> None:
    with pytest.raises(NotFoundError):
        await repos.threads.add(make_thread("ghost"))


async def test_update_replaces_mutable_fields(repos: Repos) -> None:
    thread = make_thread(await add_user(repos))
    await repos.threads.add(thread)
    changed = make_thread(
        thread.user_id,
        id=thread.id,
        title="Meet Ramesh",
        status=ThreadStatus.SNOOZED,
        check_in_after=at(100),
        times_offered=1,
    )

    await repos.threads.update(changed)

    assert await repos.threads.get(thread.id) == changed
    with pytest.raises(NotFoundError):
        await repos.threads.update(make_thread(thread.user_id, id="missing"))


async def test_list_active_skips_closed_threads_oldest_first(repos: Repos) -> None:
    user_id, other_id = await add_user(repos), await add_user(repos)
    statuses = [
        (2, ThreadStatus.IN_PROGRESS),
        (1, ThreadStatus.OPEN),
        (3, ThreadStatus.SNOOZED),
        (4, ThreadStatus.DONE),
        (5, ThreadStatus.NOT_DONE),
        (6, ThreadStatus.CANCELLED),
    ]
    for minute, status in statuses:
        await repos.threads.add(
            make_thread(user_id, id=f"t{minute}", status=status, created_at=at(minute))
        )
    await repos.threads.add(make_thread(other_id, created_at=at(0)))

    active = await repos.threads.list_active(user_id, limit=10)

    assert [t.id for t in active] == ["t1", "t2", "t3"]
    assert len(await repos.threads.list_active(user_id, limit=2)) == 2


async def test_events_return_the_latest_oldest_first(repos: Repos) -> None:
    thread = make_thread(await add_user(repos))
    await repos.threads.add(thread)
    other = make_thread(thread.user_id)
    await repos.threads.add(other)
    for minute, text in ((3, "Called him"), (1, "Wants to call"), (2, "Plans to call")):
        status = ThreadStatus.DONE if minute == 3 else None
        await repos.threads.add_event(
            ThreadEvent(f"e{minute}", thread.id, text, at(minute), status)
        )
    await repos.threads.add_event(ThreadEvent("x", other.id, "Other", at(9)))

    events = await repos.threads.events(thread.id, limit=2)

    assert [(e.text, e.status) for e in events] == [
        ("Plans to call", None),
        ("Called him", ThreadStatus.DONE),
    ]


async def test_event_for_unknown_thread_raises(repos: Repos) -> None:
    with pytest.raises(NotFoundError):
        await repos.threads.add_event(ThreadEvent("e", "missing", "x", at(0)))


async def test_count_about_counts_threads_about_a_person(repos: Repos) -> None:
    user_id = await add_user(repos)
    ramesh = Person("p1", user_id, "Ramesh", at(0))
    await repos.people.add(ramesh)
    for status in (ThreadStatus.OPEN, ThreadStatus.DONE):
        await repos.threads.add(make_thread(user_id, status=status, about_person_id="p1"))
    await repos.threads.add(make_thread(user_id))

    assert await repos.threads.count_about("p1") == 2
    assert await repos.threads.count_about("nobody") == 0


async def test_delete_removes_events_and_boundaries_and_returns_its_calls(repos: Repos) -> None:
    user_id = await add_user(repos)
    started, later, unrelated = (make_session(user_id, started_at=at(m)) for m in (0, 1, 2))
    for session in (started, later, unrelated):
        await repos.sessions.add(session)
    thread = make_thread(user_id, source_session_id=started.id)
    other = make_thread(user_id, source_session_id=unrelated.id)
    for t in (thread, other):
        await repos.threads.add(t)
    await repos.threads.add_event(ThreadEvent("e1", thread.id, "Called", at(1), None, later.id))
    await repos.threads.add_event(ThreadEvent("e2", thread.id, "Again", at(2), None, later.id))
    await repos.threads.add_event(ThreadEvent("e3", thread.id, "No call", at(3)))
    await repos.threads.add_event(ThreadEvent("e4", other.id, "Kept", at(4)))
    fenced = make_boundary(user_id, thread_id=thread.id)
    topic = make_boundary(user_id)
    for boundary in (fenced, topic):
        await repos.boundaries.add(boundary)

    sessions = await repos.threads.delete(thread.id)

    assert sessions == sorted([started.id, later.id])
    assert await repos.threads.get(thread.id) is None
    assert await repos.threads.events(thread.id, limit=10) == []
    assert [e.text for e in await repos.threads.events(other.id, limit=10)] == ["Kept"]
    assert await repos.boundaries.get(fenced.id) is None
    assert await repos.boundaries.get(topic.id) == topic
    assert await repos.threads.delete("missing") == []


async def test_touched_in_lists_threads_started_or_updated_in_a_call(repos: Repos) -> None:
    user_id = await add_user(repos)
    call, other_call = make_session(user_id), make_session(user_id)
    for session in (call, other_call):
        await repos.sessions.add(session)
    started = make_thread(user_id, source_session_id=call.id, created_at=at(2))
    updated = make_thread(user_id, source_session_id=other_call.id, created_at=at(1))
    untouched = make_thread(user_id, source_session_id=other_call.id)
    for thread in (started, updated, untouched):
        await repos.threads.add(thread)
    for n in range(2):
        await repos.threads.add_event(
            ThreadEvent(f"e{n}", updated.id, "Called him", at(5), ThreadStatus.DONE, call.id)
        )

    touched = await repos.threads.touched_in(call.id)

    assert [t.id for t in touched] == [updated.id, started.id]
    assert await repos.threads.touched_in("no-call") == []
