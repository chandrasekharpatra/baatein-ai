from datetime import datetime, timedelta

import pytest

from baatein.domain.models import Sensitivity, ThreadStatus
from baatein.memory.ranker import CheckInRules, is_due, is_fenced, pick_check_in
from tests.factories import at, make_boundary, make_thread

RULES = CheckInRules(min_gap=timedelta(days=3), max_offers=3)
NOW = at(60 * 24 * 10)


def days_ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def test_scenario_1_open_storyline_is_the_check_in() -> None:
    ramesh = make_thread("u", "Reconnect with Ramesh", status=ThreadStatus.IN_PROGRESS)

    assert pick_check_in([ramesh], [], NOW, RULES) == ramesh


def test_scenario_5_waits_until_after_the_planned_sunday_call() -> None:
    sunday = make_thread("u", "Long call with daughter", check_in_after=NOW + timedelta(days=2))

    assert pick_check_in([sunday], [], NOW, RULES) is None
    assert pick_check_in([sunday], [], NOW + timedelta(days=2), RULES) == sunday


@pytest.mark.parametrize(
    "status", [ThreadStatus.CANCELLED, ThreadStatus.DONE, ThreadStatus.NOT_DONE]
)
def test_scenarios_16_and_17_closed_storylines_are_never_raised(status: ThreadStatus) -> None:
    yoga = make_thread("u", "Join yoga class", status=status)

    assert pick_check_in([yoga], [], NOW, RULES) is None


@pytest.mark.parametrize(
    "sensitivity",
    [Sensitivity.PERSONAL, Sensitivity.HEALTH, Sensitivity.GRIEF, Sensitivity.CRISIS],
)
def test_scenario_19_sensitive_storylines_wait_for_the_user(sensitivity: Sensitivity) -> None:
    barsi = make_thread("u", "Wife's death anniversary", sensitivity=sensitivity)

    assert pick_check_in([barsi], [], NOW, RULES) is None


def test_scenario_11_boundary_by_topic_or_by_thread_blocks_the_check_in() -> None:
    painting = make_thread("u", "Finish the painting")
    almari = make_thread("u", "Clean the almirah")

    assert pick_check_in([painting], [make_boundary("u", "Painting")], NOW, RULES) is None
    by_id = make_boundary("u", "cupboard", thread_id=almari.id)
    assert pick_check_in([almari], [by_id], NOW, RULES) is None
    assert pick_check_in([almari], [make_boundary("u", "painting")], NOW, RULES) == almari


def test_recently_offered_or_too_often_unanswered_is_skipped() -> None:
    recent = make_thread("u", "a", last_offered_at=days_ago(1), times_offered=1)
    rested = make_thread("u", "b", last_offered_at=days_ago(3), times_offered=1)
    ignored = make_thread("u", "c", times_offered=3)

    assert pick_check_in([recent, ignored], [], NOW, RULES) is None
    assert pick_check_in([recent, rested, ignored], [], NOW, RULES) == rested


def test_longest_waiting_dated_thread_wins_then_oldest_undated() -> None:
    undated_old = make_thread("u", "a", created_at=at(0))
    undated_new = make_thread("u", "b", created_at=at(5))
    due_late = make_thread("u", "c", check_in_after=days_ago(1))
    due_early = make_thread("u", "d", check_in_after=days_ago(2))

    assert pick_check_in([undated_new, due_late, undated_old, due_early], [], NOW, RULES) == (
        due_early
    )
    assert pick_check_in([undated_new, undated_old], [], NOW, RULES) == undated_old


def test_snoozed_thread_is_due_only_once_its_date_has_come() -> None:
    snoozed = make_thread("u", status=ThreadStatus.SNOOZED)

    assert not is_due(snoozed, NOW)
    assert not is_due(
        make_thread("u", status=ThreadStatus.SNOOZED, check_in_after=NOW + timedelta(1)), NOW
    )
    assert is_due(make_thread("u", status=ThreadStatus.SNOOZED, check_in_after=NOW), NOW)


def test_is_fenced_ignores_stopwords() -> None:
    assert not is_fenced(make_thread("u", "Call the son"), [make_boundary("u", "the")])
