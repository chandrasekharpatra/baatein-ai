from datetime import date, time

import pytest

from baatein.domain.errors import NotFoundError
from baatein.domain.models import OccurrenceStatus, Person, ReminderStatus, Repeat
from tests.contract.conftest import Repos
from tests.factories import at, make_reminder, make_session, make_user


async def add_user(repos: Repos) -> str:
    user = make_user()
    await repos.users.add(user)
    return user.id


async def test_add_then_get_round_trips(repos: Repos) -> None:
    user_id = await add_user(repos)
    session = make_session(user_id)
    await repos.sessions.add(session)
    await repos.people.add(Person("p1", user_id, "Ravi", at(0)))
    reminder = make_reminder(
        user_id,
        on=date(2026, 11, 18),
        at=time(9, 30),
        repeat=Repeat.YEARLY,
        alert_days_before=(7, 0),
        note="Buy a gift",
        about_person_id="p1",
        source_session_id=session.id,
    )

    await repos.reminders.add(reminder)

    assert await repos.reminders.get(reminder.id) == reminder
    assert await repos.reminders.get("missing") is None
    assert await repos.reminders.count_about("p1") == 1
    assert await repos.reminders.count_about("nobody") == 0


async def test_undated_reminder_round_trips(repos: Repos) -> None:
    reminder = make_reminder(await add_user(repos), status=ReminderStatus.NEEDS_INFO)

    await repos.reminders.add(reminder)

    assert await repos.reminders.get(reminder.id) == reminder


async def test_add_for_unknown_user_raises(repos: Repos) -> None:
    with pytest.raises(NotFoundError):
        await repos.reminders.add(make_reminder("ghost"))


async def test_update_changes_it_in_place(repos: Repos) -> None:
    reminder = make_reminder(await add_user(repos), on=date(2026, 11, 7))
    await repos.reminders.add(reminder)
    moved = make_reminder(
        reminder.user_id,
        id=reminder.id,
        title="Pick up glasses",
        on=date(2026, 11, 9),
        at=time(11, 0),
        alert_days_before=(1,),
    )

    await repos.reminders.update(moved)

    assert await repos.reminders.get(reminder.id) == moved
    with pytest.raises(NotFoundError):
        await repos.reminders.update(make_reminder(reminder.user_id, id="missing"))


async def test_list_open_skips_done_and_cancelled_oldest_first(repos: Repos) -> None:
    user_id, other_id = await add_user(repos), await add_user(repos)
    for minute, status in (
        (2, ReminderStatus.ACTIVE),
        (1, ReminderStatus.NEEDS_INFO),
        (3, ReminderStatus.DONE),
        (4, ReminderStatus.CANCELLED),
    ):
        await repos.reminders.add(
            make_reminder(user_id, id=f"r{minute}", status=status, created_at=at(minute))
        )
    await repos.reminders.add(make_reminder(other_id))

    assert [r.id for r in await repos.reminders.list_open(user_id, limit=10)] == ["r1", "r2"]
    assert len(await repos.reminders.list_open(user_id, limit=1)) == 1


async def test_settle_marks_one_occurrence_and_can_change_it(repos: Repos) -> None:
    reminder = make_reminder(await add_user(repos), repeat=Repeat.MONTHLY_WEEKDAY)
    other = make_reminder(reminder.user_id)
    for r in (reminder, other):
        await repos.reminders.add(r)

    await repos.reminders.settle(reminder.id, date(2026, 12, 6), OccurrenceStatus.SKIPPED, at(1))
    await repos.reminders.settle(reminder.id, date(2027, 1, 3), OccurrenceStatus.SKIPPED, at(2))
    await repos.reminders.settle(reminder.id, date(2027, 1, 3), OccurrenceStatus.DONE, at(3))
    await repos.reminders.settle(other.id, date(2026, 12, 6), OccurrenceStatus.DONE, at(4))

    assert await repos.reminders.settled(reminder.id) == {
        date(2026, 12, 6): OccurrenceStatus.SKIPPED,
        date(2027, 1, 3): OccurrenceStatus.DONE,
    }
    with pytest.raises(NotFoundError):
        await repos.reminders.settle("missing", date(2026, 1, 1), OccurrenceStatus.DONE, at(0))


async def test_created_in_lists_open_reminders_from_a_call(repos: Repos) -> None:
    user_id = await add_user(repos)
    call, other_call = make_session(user_id), make_session(user_id)
    for session in (call, other_call):
        await repos.sessions.add(session)
    for rid, session_id, status, minute in (
        ("later", call.id, ReminderStatus.NEEDS_INFO, 2),
        ("first", call.id, ReminderStatus.ACTIVE, 1),
        ("cancelled", call.id, ReminderStatus.CANCELLED, 3),
        ("elsewhere", other_call.id, ReminderStatus.ACTIVE, 4),
    ):
        await repos.reminders.add(
            make_reminder(
                user_id, id=rid, status=status, source_session_id=session_id, created_at=at(minute)
            )
        )

    assert [r.id for r in await repos.reminders.created_in(call.id)] == ["first", "later"]
