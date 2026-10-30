""" "Forget me" erases what Baatein keeps about a user, for both storage backends."""

from datetime import date

from baatein.domain.ids import new_id
from baatein.domain.models import DeclinedSuggestion, OccurrenceStatus, Person, ThreadEvent
from tests.contract.conftest import Repos
from tests.factories import (
    T0,
    make_boundary,
    make_reminder,
    make_session,
    make_thread,
    make_turn,
    make_user,
)


async def remembered(repos: Repos) -> str:
    """A user with one of everything Baatein keeps. Returns the user's id."""
    user = make_user()
    await repos.users.add(user)
    session = make_session(user.id)
    await repos.sessions.add(session)
    await repos.turns.append(make_turn(session.id, 1, "Ramesh se baat hui"))
    person = Person(new_id(), user.id, "Ramesh", T0, "college friend")
    await repos.people.add(person)
    thread = make_thread(user.id, about_person_id=person.id)
    await repos.threads.add(thread)
    await repos.threads.add_event(ThreadEvent(new_id(), thread.id, "Called Ramesh", T0))
    await repos.boundaries.add(make_boundary(user.id))
    await repos.suggestions.add(DeclinedSuggestion(new_id(), user.id, "go for a walk", T0))
    reminder = make_reminder(user.id)
    await repos.reminders.add(reminder)
    await repos.reminders.settle(reminder.id, date(2026, 11, 1), OccurrenceStatus.DONE, T0)
    return user.id


async def test_erase_memories_keeps_the_account_and_other_users(repos: Repos) -> None:
    user_id, other_id = await remembered(repos), await remembered(repos)
    [call] = await repos.sessions.recent_for_user(user_id, 5)

    await repos.users.erase_memories(user_id)

    assert await repos.users.get(user_id) is not None
    assert await repos.sessions.recent_for_user(user_id, 5) == []
    assert await repos.turns.list_for_session(call.id) == []
    assert await repos.people.list_for_user(user_id, 5) == []
    assert await repos.threads.list_active(user_id, 5) == []
    assert await repos.suggestions.list_for_user(user_id, 5) == []
    assert await repos.reminders.list_open(user_id, 5) == []
    assert await repos.boundaries.list_active(user_id, "any", T0) == []
    assert len(await repos.people.list_for_user(other_id, 5)) == 1
    assert len(await repos.reminders.list_open(other_id, 5)) == 1
