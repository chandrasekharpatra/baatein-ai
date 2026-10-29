from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from baatein.domain.models import (
    BoundaryScope,
    Certainty,
    DeclinedSuggestion,
    MemoryKind,
    Person,
    Repeat,
    ScoredFact,
    Sensitivity,
    ThreadEvent,
    ThreadStatus,
)
from baatein.memory.context_builder import (
    MAX_SUMMARY_CHARS,
    render_context,
    render_memory,
    render_recall,
    render_reminder,
    render_search,
    render_thread,
)
from baatein.memory.service import DueReminder, MemoryContext
from tests.factories import (
    T0,
    make_boundary,
    make_fact,
    make_reminder,
    make_session,
    make_thread,
)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 29, 19, 40, tzinfo=IST)
RAMESH = Person("p1", "u", "Ramesh", T0, relation="college friend")


def context(**overrides: object) -> MemoryContext:
    fields: dict[str, object] = {
        "now": NOW,
        "last_call": None,
        "facts": [],
        "people": [],
        "threads": [],
        "check_in": None,
        "check_in_events": [],
        "boundaries": [],
        "declined": [],
    }
    return MemoryContext(**(fields | overrides))  # type: ignore[arg-type]


def test_first_call_says_so_with_local_time() -> None:
    text = render_context(context())

    assert text.startswith(
        "Now: Thu 29 Oct 2026, 7:40 pm (Asia/Kolkata). Coming days: Fri 30 Oct, Sat 31 Oct, "
        "Sun 1 Nov, Mon 2 Nov, Tue 3 Nov, Wed 4 Nov, Thu 5 Nov."
    )
    assert "This is your first call with them." in text
    assert "nothing saved about this user" in text


def test_time_formats_midnight_and_noon() -> None:
    assert "12:05 am" in render_context(context(now=NOW.replace(hour=0, minute=5)))
    assert "12:00 pm" in render_context(context(now=NOW.replace(hour=12, minute=0)))


def test_last_call_shows_how_long_ago_and_its_summary() -> None:
    def last_call_line(days: int, **fields: object) -> str:
        session = make_session("u", started_at=NOW - timedelta(days=days), **fields)
        return render_context(context(last_call=session)).splitlines()[1]

    assert last_call_line(0) == "Your last call was earlier today (Thu 29 Oct 2026)."
    assert last_call_line(1).startswith("Your last call was yesterday")
    long = last_call_line(2, summary="x" * (MAX_SUMMARY_CHARS + 50))
    assert long == f"Your last call was 2 days ago (Tue 27 Oct 2026). Summary: {'x' * 400}"


def test_call_cut_off_mid_story_is_offered_once() -> None:
    session = make_session(
        "u",
        started_at=NOW - timedelta(days=1),
        ended_abruptly=True,
        unfinished_topic="Gaon ka mela",
    )

    text = render_context(context(last_call=session))

    assert 'It cut off while they were saying: "Gaon ka mela". Offer once' in text
    abrupt_without_topic = make_session("u", started_at=NOW, ended_abruptly=True)
    assert "cut off" not in render_context(context(last_call=abrupt_without_topic))


def test_boundaries_and_declined_suggestions_are_always_listed() -> None:
    boundaries = [
        make_boundary("u", "painting", scope=BoundaryScope.THIS_CALL),
        make_boundary("u", "almari"),
        make_boundary(
            "u", "doctor", scope=BoundaryScope.UNTIL_DATE, until=datetime(2026, 11, 6, tzinfo=IST)
        ),
        make_boundary("u", "odd", scope=BoundaryScope.UNTIL_DATE),
    ]
    walk = DeclinedSuggestion("s1", "u", "go for a walk", T0)

    text = render_context(context(boundaries=boundaries, declined=[walk]))

    assert f"- [{boundaries[0].id}] painting (this call only)" in text
    assert f"- [{boundaries[1].id}] almari (until they reopen it)" in text
    assert f"- [{boundaries[2].id}] doctor (until Fri 6 Nov 2026)" in text
    assert f"- [{boundaries[3].id}] odd (for now)" in text
    assert "lift_boundary" in text
    assert "Never make them again:\n- go for a walk" in text


def test_memories_people_and_storylines_with_ids() -> None:
    fact = make_fact("u", "Misses Ramesh", about_person_id="p1")
    thread = make_thread("u", "Reconnect with Ramesh", about_person_id="p1")

    text = render_context(context(facts=[fact], people=[RAMESH], threads=[thread]))

    assert f"- [{fact.id}] Misses Ramesh (about Ramesh (college friend))" in text
    assert "People in the user's life: Ramesh (college friend)" in text
    assert "Don't raise these yourself" in text
    assert f"- [{thread.id}] Reconnect with Ramesh (about Ramesh (college friend))" in text
    assert "check-in" not in text
    assert "nothing saved" not in text


def test_one_check_in_with_its_recent_events_and_not_listed_twice() -> None:
    check_in = make_thread(
        "u", "Reconnect with Ramesh", status=ThreadStatus.IN_PROGRESS, next_step="Call him"
    )
    other = make_thread("u", "Sing for granddaughter")
    event = ThreadEvent("e1", check_in.id, "Plans to call him", datetime(2026, 10, 27, tzinfo=IST))

    text = render_context(
        context(threads=[check_in, other], check_in=check_in, check_in_events=[event])
    )

    assert "Today's one check-in." in text
    assert 'accept "nahi"' in text
    assert text.count(check_in.id) == 1
    assert f"- [{check_in.id}] Reconnect with Ramesh (in progress; next: Call him)" in text
    assert "  Tue 27 Oct 2026: Plans to call him" in text
    assert f"- [{other.id}] Sing for granddaughter" in text


def test_thread_line_notes_snooze_date_and_sensitivity() -> None:
    snoozed = make_thread(
        "u",
        "Apologise to brother",
        status=ThreadStatus.SNOOZED,
        check_in_after=datetime(2026, 11, 2, tzinfo=IST),
        sensitivity=Sensitivity.PERSONAL,
    )

    assert render_thread(snoozed, tz=IST) == (
        f"- [{snoozed.id}] Apologise to brother (snoozed until Mon 2 Nov 2026; personal: only if"
        " the user brings it up)"
    )
    undated = make_thread("u", "x", status=ThreadStatus.SNOOZED)
    assert render_thread(undated).endswith("x (snoozed)")


def test_memory_line_notes_kind_sensitivity_and_uncertainty() -> None:
    fact = make_fact(
        "u",
        "Gets dizzy sometimes",
        kind=MemoryKind.STORY,
        sensitivity=Sensitivity.HEALTH,
        certainty=Certainty.UNCERTAIN,
    )

    line = render_memory(fact)

    assert line == (
        f"- [{fact.id}] Gets dizzy sometimes (story; health: only if the user brings it up;"
        " the user wasn't sure)"
    )


def test_memory_line_without_person_relation_uses_name_only() -> None:
    fact = make_fact("u", "Neighbour", about_person_id="p2")
    sunita = Person("p2", "u", "Sunita", T0)

    assert render_memory(fact, {"p2": sunita}).endswith("(about Sunita)")


def test_recall_is_empty_without_facts() -> None:
    assert render_recall([]) is None


def test_recall_lists_memories_with_ids() -> None:
    fact = make_fact("u", "Has a dog named Bruno")

    text = render_recall([ScoredFact(fact, 0.5)])

    assert text is not None
    assert f"- [{fact.id}] Has a dog named Bruno" in text


def test_search_reports_nothing_saved() -> None:
    assert render_search([]).startswith("Nothing saved about that.")


def test_search_lists_memories_with_people() -> None:
    fact = make_fact("u", "Wants to call Ramesh again", about_person_id="p1")

    text = render_search([ScoredFact(fact, 0.4)], [RAMESH])

    assert text == f"- [{fact.id}] Wants to call Ramesh again (about Ramesh (college friend))"


def test_reminder_line_shows_day_time_repeat_alerts_and_note() -> None:
    birthday = make_reminder(
        "u",
        on=date(2026, 11, 18),
        repeat=Repeat.YEARLY,
        alert_days_before=(7, 0),
        note="Buy a gift",
    )

    assert render_reminder(birthday) == (
        f"- [{birthday.id}] Ravi's birthday (on Wed 18 Nov 2026, 9:00 am; every year; remind 7"
        " days before; Buy a gift)"
    )
    assert render_reminder(birthday, date(2027, 11, 18)).startswith(
        f"- [{birthday.id}] Ravi's birthday (on Thu 18 Nov 2027, 9:00 am"
    )
    evening = make_reminder("u", "Walk", on=date(2026, 11, 2), at=time(18, 30))
    assert render_reminder(evening) == f"- [{evening.id}] Walk (on Mon 2 Nov 2026, 6:30 pm)"
    assert render_reminder(make_reminder("u", "Trip")).endswith("] Trip")
    checkup = make_reminder("u", "Checkup", on=date(2026, 11, 2), alert_days_before=(1,))
    assert render_reminder(checkup).endswith("; remind 1 day before)")


def test_reminders_are_split_into_due_coming_and_undated() -> None:
    birthday = make_reminder("u", on=date(2026, 11, 18), repeat=Repeat.YEARLY)
    dentist = make_reminder("u", "Dentist", on=date(2026, 11, 2))
    trip = make_reminder("u", "June trip")

    text = render_context(
        context(
            reminders=[
                DueReminder(birthday, date(2026, 11, 18), alerted=True),
                DueReminder(dentist, date(2026, 11, 2), alerted=False),
            ],
            undated_reminders=[trip],
        )
    )

    due, coming, undated = (text.index(h) for h in ("due now", "coming up", "waiting for"))
    assert due < text.index(birthday.id) < coming < text.index(dentist.id) < undated
    assert text.index(trip.id) > undated
    assert "complete_occurrence" in text
    assert "Reminders" not in render_context(context())
