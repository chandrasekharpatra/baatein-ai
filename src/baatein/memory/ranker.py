"""Picks the one storyline Baatein may check in on at the start of a call.

The scenario library's rule is "never mechanically at every opening": at most one check-in
per call, never a task to enforce, and never about something the user fenced off.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from baatein.domain.models import Boundary, Sensitivity, Thread, ThreadStatus
from baatein.text import search_terms


@dataclass(frozen=True)
class CheckInRules:
    min_gap: timedelta
    """Don't offer the same thread again sooner than this."""
    max_offers: int
    """Stop offering a thread after this many offers without an answer."""


def is_due(thread: Thread, now: datetime) -> bool:
    """Open or in progress and past its check-in date, or snoozed until a date that has come."""
    if thread.status in (ThreadStatus.OPEN, ThreadStatus.IN_PROGRESS):
        return thread.check_in_after is None or thread.check_in_after <= now
    if thread.status is ThreadStatus.SNOOZED:
        return thread.check_in_after is not None and thread.check_in_after <= now
    return False


def is_fenced(thread: Thread, boundaries: Iterable[Boundary]) -> bool:
    """A boundary names the thread, or shares a word with its title ("painting")."""
    title = set(search_terms(thread.title))
    return any(b.thread_id == thread.id or title & set(search_terms(b.topic)) for b in boundaries)


def pick_check_in(
    threads: Iterable[Thread],
    boundaries: Iterable[Boundary],
    now: datetime,
    rules: CheckInRules,
) -> Thread | None:
    """The single thread to bring up this call, or None.

    Skips threads that aren't due, are sensitive (grief and health only if the user raises
    them), are under a boundary, were offered recently, or were offered too often without an
    answer. Of the rest, the longest-waiting dated one wins, then the oldest undated one.
    """
    boundaries = list(boundaries)
    candidates = [
        t
        for t in threads
        if is_due(t, now)
        and t.sensitivity is Sensitivity.NORMAL
        and not is_fenced(t, boundaries)
        and (t.last_offered_at is None or now - t.last_offered_at >= rules.min_gap)
        and t.times_offered < rules.max_offers
    ]
    return min(
        candidates,
        key=lambda t: (t.check_in_after is None, t.check_in_after or t.created_at, t.times_offered),
        default=None,
    )
