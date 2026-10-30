from datetime import UTC, date, datetime, time, timedelta

from baatein.accounts import to_seed
from baatein.domain.models import (
    BoundaryScope,
    DeclinedSuggestion,
    OccurrenceStatus,
    Person,
    ReminderStatus,
    ThreadEvent,
)
from baatein.storage.sqlite.accounts import AccountData
from tests.factories import (
    make_boundary,
    make_fact,
    make_reminder,
    make_session,
    make_thread,
    make_turn,
    make_user,
)

NOW = datetime(2026, 1, 10, 6, 30, tzinfo=UTC)  # 12:00 in India


def account(**parts: object) -> AccountData:
    fields: dict[str, object] = {
        "user": make_user(id="u", email="mohan@example.com"),
        "people": [],
        "sessions": [],
        "turns": {},
        "facts": [],
        "threads": [],
        "events": {},
        "boundaries": [],
        "declined": [],
        "reminders": [],
        "settled": {},
    }
    return AccountData(**(fields | parts))  # type: ignore[arg-type]


def test_a_plain_account_is_just_its_user() -> None:
    seed = to_seed(account(), NOW)

    assert seed.model_dump(mode="json", exclude_defaults=True) == {
        "user": {"email": "mohan@example.com"},
        "persona": "casual",
    }


def test_forgotten_memories_stay_forgotten_and_corrections_keep_their_history() -> None:
    gone = make_fact("u", "forgotten", deleted_at=NOW)
    noida = make_fact("u", "Ravi lives in Noida", retired_at=NOW)
    dwarka = make_fact("u", "Ravi lives in Dwarka")
    noida = noida.__class__(**{**vars(noida), "superseded_by": dwarka.id})
    old_job = make_fact("u", "Works at a bank", retired_at=NOW)
    forgotten_job = make_fact("u", "Works at a school", deleted_at=NOW)
    old_job = old_job.__class__(**{**vars(old_job), "superseded_by": forgotten_job.id})

    seed = to_seed(account(facts=[dwarka, gone, noida, old_job, forgotten_job]), NOW)

    assert [(m.key, m.corrects, m.text) for m in seed.memories] == [
        ("m1", None, "Ravi lives in Noida"),
        (None, "m1", "Ravi lives in Dwarka"),
    ]


def test_only_calls_with_something_in_them_are_kept() -> None:
    silent = make_session("u", started_at=NOW - timedelta(days=1))
    talked = make_session(
        "u", started_at=NOW - timedelta(days=3), ended_at=NOW - timedelta(days=3, minutes=-20)
    )
    unfinished = make_session("u", started_at=NOW - timedelta(minutes=5))
    fact = make_fact("u", source_session_id=unfinished.id)

    seed = to_seed(
        account(
            sessions=[talked, silent, unfinished],
            turns={talked.id: [make_turn(talked.id, 1, "Namaste")]},
            facts=[fact],
        ),
        NOW,
    )

    assert [(c.key, c.days_ago, c.at, c.minutes) for c in seed.calls] == [
        ("c1", 3, "12:00", 20),
        ("c2", 0, "11:55", 1),
    ]
    assert seed.calls[0].turns[0].text == "Namaste"
    assert seed.memories[0].call == "c2"


def test_topics_storylines_suggestions_and_reminders() -> None:
    call = make_session("u", started_at=NOW - timedelta(days=1), summary="Chat")
    thread = make_thread(
        "u",
        "Painting",
        last_offered_at=NOW - timedelta(days=2),
        times_offered=1,
        check_in_after=datetime(2026, 1, 12, tzinfo=UTC),
    )
    untouched = make_thread("u", "Yoga")
    events = {
        thread.id: [
            ThreadEvent("e1", thread.id, "Bought brushes", NOW - timedelta(days=4)),
            ThreadEvent("e2", thread.id, "Painted", NOW, session_id=call.id),
        ]
    }
    boundaries = [
        make_boundary("u", "painting", thread_id=thread.id),
        make_boundary("u", "lifted", lifted_at=NOW),
        make_boundary("u", "today only", scope=BoundaryScope.THIS_CALL, session_id=call.id),
        make_boundary("u", "land", scope=BoundaryScope.UNTIL_DATE, until=NOW + timedelta(days=3)),
    ]
    declined = [
        DeclinedSuggestion("d1", "u", "walk", NOW),
        DeclinedSuggestion("d2", "u", "yoga", NOW, session_id=call.id),
    ]
    pills = make_reminder(
        "u", "Pills", on=date(2026, 1, 8), at=time(8, 30), alert_days_before=(1, 0)
    )
    trip = make_reminder("u", "Trip", status=ReminderStatus.DONE, on=date(2026, 1, 9))

    seed = to_seed(
        account(
            people=[Person("p", "u", "Ravi", NOW, "son")],
            sessions=[call],
            threads=[thread, untouched],
            events=events,
            boundaries=boundaries,
            declined=declined,
            reminders=[pills, trip],
            settled={pills.id: {date(2026, 1, 8): OccurrenceStatus.SKIPPED}},
        ),
        NOW,
    )
    dumped = seed.model_dump(mode="json", exclude_defaults=True)

    assert dumped["people"] == [{"name": "Ravi", "relation": "son"}]
    assert dumped["threads"][0] == {
        "key": "t1",
        "title": "Painting",
        "check_in_after": "today+2",
        "last_offered_days_ago": 2,
        "events": [
            {"text": "Bought brushes", "days_ago": 4},
            {"text": "Painted", "call": "c1"},
        ],
    }
    assert dumped["threads"][1] == {"title": "Yoga"}
    assert dumped["boundaries"] == [
        {"topic": "painting", "thread": "t1"},
        {"topic": "land", "scope": "until_date", "until": "today+3"},
    ]
    assert dumped["declined_suggestions"] == ["walk", {"text": "yoga", "call": "c1"}]
    assert dumped["reminders"] == [
        {
            "title": "Pills",
            "date": "today-2",
            "time": "08:30",
            "days_before": [1, 0],
            "settled": [{"date": "today-2", "status": "skipped"}],
        },
        {"title": "Trip", "date": "today-1", "status": "done"},
    ]
