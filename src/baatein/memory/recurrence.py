"""When a reminder happens, and which occurrences to bring up in a call.

All dates are in the user's timezone. A yearly 29 February falls on 28 February in other
years, and a monthly 31st on the last day of shorter months.
"""

import calendar
from collections.abc import Collection, Iterator
from datetime import date, datetime, timedelta, tzinfo

from baatein.domain.models import Reminder, Repeat

MAX_YEARS = 50


def _clamped(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def week_of_month(day: date) -> int:
    """1 to 4 for the first to fourth weekday of its kind in the month, -1 for a fifth (last)."""
    nth = (day.day - 1) // 7 + 1
    return -1 if nth == 5 else nth


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """The nth (1-4, or -1 for last) given weekday of a month."""
    days = calendar.monthrange(year, month)[1]
    matching = [d for d in range(1, days + 1) if date(year, month, d).weekday() == weekday]
    return date(year, month, matching[nth - 1 if nth > 0 else -1])


def _month_steps(start: date) -> Iterator[tuple[int, int]]:
    year, month = start.year, start.month
    while True:
        yield year, month
        month += 1
        if month == 13:
            year, month = year + 1, 1


def occurrences(reminder: Reminder, start: date, end: date) -> Iterator[date]:
    """The event dates of a reminder from `start` to `end`, inclusive, in order."""
    first = reminder.on
    if first is None:
        return
    if reminder.repeat is Repeat.ONCE:
        candidates: Iterator[date] = iter([first])
    elif reminder.repeat is Repeat.WEEKLY:
        skip = max(0, (start - first).days // 7)
        candidates = (first + timedelta(weeks=skip + n) for n in range(MAX_YEARS * 53))
    elif reminder.repeat is Repeat.YEARLY:
        candidates = (
            _clamped(year, first.month, first.day)
            for year in range(max(first.year, start.year), first.year + MAX_YEARS)
        )
    elif reminder.repeat is Repeat.MONTHLY_DATE:
        candidates = (_clamped(y, m, first.day) for y, m in _month_steps(max(first, start)))
    else:
        nth = week_of_month(first)
        candidates = (
            nth_weekday(y, m, first.weekday(), nth) for y, m in _month_steps(max(first, start))
        )
    for day in candidates:
        if day > end:
            return
        if day >= max(start, first):
            yield day


def due(
    reminder: Reminder,
    settled: Collection[date],
    since: datetime,
    now: datetime,
    tz: tzinfo,
    horizon: timedelta = timedelta(days=7),
) -> list[tuple[date, bool]]:
    """Occurrences worth knowing about in a call at `now`, each with whether it is *alerted*.

    An occurrence counts if it is today or later and isn't done or skipped (`settled`). It is
    alerted (bring it up now) if one of its alert times fell since the last call (`since`).
    Otherwise it counts as coming up if an alert or the day itself falls within `horizon`.

    An alert that had already passed when the reminder was made (a reminder for later today,
    or "a day before" asked on that day) falls when it was made, so the next call brings it up.
    """
    if not reminder.is_open:
        return []
    today = now.astimezone(tz).date()
    horizon_end = now + horizon
    lead = timedelta(days=max(reminder.alert_days_before, default=0))
    found = []
    for day in occurrences(reminder, today, horizon_end.astimezone(tz).date() + lead):
        if day in settled:
            continue
        alerts = [
            max(datetime.combine(day - timedelta(days=d), reminder.at, tz), reminder.created_at)
            for d in reminder.alert_days_before
        ]
        alerted = any(since < alert <= now for alert in alerts)
        soon = day <= horizon_end.astimezone(tz).date() or any(
            now < alert <= horizon_end for alert in alerts
        )
        if alerted or soon:
            found.append((day, alerted))
    return found


def nearest_open(reminder: Reminder, settled: Collection[date], today: date) -> date | None:
    """The occurrence the user most likely means by "is baar" (this time): the open one
    closest to today within a month either side, preferring the upcoming one on a tie. A
    one-off reminder has only one occurrence, whatever its date."""
    if reminder.repeat is Repeat.ONCE:
        return reminder.on if reminder.on and reminder.on not in settled else None
    window = timedelta(days=31)
    candidates = [
        d for d in occurrences(reminder, today - window, today + window) if d not in settled
    ]
    return min(candidates, key=lambda d: (abs((d - today).days), d < today), default=None)
