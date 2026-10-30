from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from baatein.domain.models import ReminderStatus, Repeat
from baatein.memory.recurrence import (
    due,
    nearest_open,
    nth_weekday,
    occurrences,
    week_of_month,
)
from tests.factories import make_reminder

IST = ZoneInfo("Asia/Kolkata")


def dates(repeat: Repeat, first: date, start: date, end: date) -> list[date]:
    return list(occurrences(make_reminder("u", on=first, repeat=repeat), start, end))


def test_once_falls_only_on_its_day() -> None:
    day = date(2026, 11, 18)

    assert dates(Repeat.ONCE, day, date(2026, 11, 1), date(2026, 11, 30)) == [day]
    assert dates(Repeat.ONCE, day, date(2026, 11, 19), date(2026, 12, 30)) == []
    assert dates(Repeat.ONCE, day, date(2026, 11, 1), date(2026, 11, 17)) == []
    assert list(occurrences(make_reminder("u"), date(2026, 1, 1), date(2027, 1, 1))) == []


def test_scenario_34_yearly_birthday() -> None:
    found = dates(Repeat.YEARLY, date(2026, 11, 18), date(2026, 1, 1), date(2028, 12, 31))

    assert found == [date(2026, 11, 18), date(2027, 11, 18), date(2028, 11, 18)]


def test_yearly_29_february_falls_on_28th_in_other_years() -> None:
    found = dates(Repeat.YEARLY, date(2028, 2, 29), date(2028, 1, 1), date(2030, 12, 31))

    assert found == [date(2028, 2, 29), date(2029, 2, 28), date(2030, 2, 28)]


def test_weekly_from_any_start() -> None:
    first = date(2026, 11, 2)

    found = dates(Repeat.WEEKLY, first, date(2026, 11, 20), date(2026, 12, 1))

    assert found == [date(2026, 11, 23), date(2026, 11, 30)]
    assert dates(Repeat.WEEKLY, first, date(2026, 10, 1), date(2026, 11, 9)) == [
        first,
        date(2026, 11, 9),
    ]


def test_monthly_31st_falls_on_the_last_day_of_shorter_months() -> None:
    found = dates(Repeat.MONTHLY_DATE, date(2027, 1, 31), date(2027, 1, 1), date(2027, 4, 30))

    assert found == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)]


def test_scenario_44_first_sunday_of_every_month() -> None:
    first_sunday = date(2026, 11, 1)

    found = dates(Repeat.MONTHLY_WEEKDAY, first_sunday, date(2026, 11, 2), date(2027, 2, 28))

    assert found == [date(2026, 12, 6), date(2027, 1, 3), date(2027, 2, 7)]


def test_fifth_weekday_means_the_last_one() -> None:
    fifth_monday = date(2026, 11, 30)

    assert week_of_month(fifth_monday) == -1
    assert week_of_month(date(2026, 11, 9)) == 2
    assert dates(Repeat.MONTHLY_WEEKDAY, fifth_monday, date(2026, 12, 1), date(2027, 1, 31)) == [
        date(2026, 12, 28),
        date(2027, 1, 25),
    ]
    assert nth_weekday(2026, 11, 6, 1) == date(2026, 11, 1)


def ist(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour), IST)


BIRTHDAY = make_reminder("u", on=date(2026, 11, 18), repeat=Repeat.YEARLY, alert_days_before=(7, 0))


@pytest.mark.parametrize(
    ("since", "now", "expected"),
    [
        # Scenario #34: the call on 12 Nov after the 11 Nov 9 am alert.
        (ist(date(2026, 11, 5)), ist(date(2026, 11, 12)), [(date(2026, 11, 18), True)]),
        # Already told on the 11th; on the 12th it is just coming up within the week.
        (ist(date(2026, 11, 11)), ist(date(2026, 11, 12)), [(date(2026, 11, 18), False)]),
        # A week before the first alert: the alert falls within the horizon.
        (ist(date(2026, 11, 1)), ist(date(2026, 11, 5)), [(date(2026, 11, 18), False)]),
        # Far away, and the day itself is past.
        (ist(date(2026, 9, 1)), ist(date(2026, 9, 2)), []),
        (ist(date(2026, 11, 17)), ist(date(2026, 11, 19)), []),
    ],
    ids=["alerted", "told-already", "alert-soon", "far", "past"],
)
def test_due(since: datetime, now: datetime, expected: list[tuple[date, bool]]) -> None:
    assert due(BIRTHDAY, set(), since, now, IST) == expected


def test_due_skips_settled_occurrences_and_closed_reminders() -> None:
    since, now = ist(date(2026, 11, 5)), ist(date(2026, 11, 12))

    assert due(BIRTHDAY, {date(2026, 11, 18)}, since, now, IST) == []
    cancelled = make_reminder("u", on=date(2026, 11, 18), status=ReminderStatus.CANCELLED)
    assert due(cancelled, set(), since, now, IST) == []


def test_due_includes_the_day_itself_without_lead_alerts() -> None:
    bill = make_reminder("u", on=date(2026, 11, 20), at=time(9, 0))

    assert due(bill, set(), ist(date(2026, 11, 14)), ist(date(2026, 11, 15)), IST) == [
        (date(2026, 11, 20), False)
    ]
    on_the_day = due(bill, set(), ist(date(2026, 11, 19)), ist(date(2026, 11, 20), 10), IST)
    assert on_the_day == [(date(2026, 11, 20), True)]


def test_nearest_open_prefers_the_closest_then_upcoming() -> None:
    monthly = make_reminder("u", on=date(2026, 10, 5), repeat=Repeat.MONTHLY_DATE)

    assert nearest_open(monthly, set(), date(2026, 11, 3)) == date(2026, 11, 5)
    assert nearest_open(monthly, set(), date(2026, 11, 8)) == date(2026, 11, 5)
    assert nearest_open(monthly, {date(2026, 11, 5)}, date(2026, 11, 8)) == date(2026, 12, 5)
    weekly = make_reminder("u", on=date(2026, 11, 2), repeat=Repeat.WEEKLY)
    assert nearest_open(weekly, set(), date(2026, 11, 6)) == date(2026, 11, 2) + timedelta(7)
    assert nearest_open(make_reminder("u"), set(), date(2026, 11, 5)) is None


def test_an_old_alert_for_an_event_beyond_the_week_is_not_repeated() -> None:
    wedding = make_reminder("u", on=date(2026, 12, 20), alert_days_before=(30,))

    assert due(wedding, set(), ist(date(2026, 11, 25)), ist(date(2026, 12, 1)), IST) == []


def test_an_alert_already_past_when_made_falls_when_it_was_made() -> None:
    # Asked at 4 pm to be reminded today: the default 9 am alert had passed, so the next call
    # today brings it up, and a call later still doesn't bring it up again.
    made = ist(date(2026, 10, 30), 16)
    today = make_reminder("u", on=date(2026, 10, 30), created_at=made)
    later = made + timedelta(minutes=1)

    assert due(today, set(), made - timedelta(minutes=2), later, IST) == [
        (date(2026, 10, 30), True)
    ]
    assert due(today, set(), later, later + timedelta(hours=1), IST) == [
        (date(2026, 10, 30), False)
    ]
