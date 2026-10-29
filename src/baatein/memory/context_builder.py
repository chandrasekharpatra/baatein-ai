from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, tzinfo

from baatein.domain.models import (
    Boundary,
    BoundaryScope,
    Certainty,
    Fact,
    MemoryKind,
    Person,
    ScoredFact,
    Sensitivity,
    Thread,
    ThreadEvent,
    ThreadStatus,
)
from baatein.memory.service import MemoryContext

MAX_SUMMARY_CHARS = 400


def _day(value: date) -> str:
    return f"{value:%a} {value.day} {value:%b %Y}"


def _time(value: datetime) -> str:
    hour = value.hour % 12 or 12
    return f"{_day(value.date())}, {hour}:{value.minute:02d} {'am' if value.hour < 12 else 'pm'}"


def _ago(days: int) -> str:
    return {0: "earlier today", 1: "yesterday"}.get(days, f"{days} days ago")


def _local_day(value: datetime, tz: tzinfo | None) -> str:
    return _day(value.astimezone(tz).date())


def _who(person: Person) -> str:
    return f"{person.name} ({person.relation})" if person.relation else person.name


def _sensitive(sensitivity: Sensitivity) -> list[str]:
    if sensitivity is Sensitivity.NORMAL:
        return []
    return [f"{sensitivity.value}: only if the user brings it up"]


def render_memory(fact: Fact, people: Mapping[str, Person] | None = None) -> str:
    """One memory as a line Claude can refer back to by id."""
    notes = []
    if fact.kind is not MemoryKind.FACT:
        notes.append(fact.kind.value)
    person = (people or {}).get(fact.about_person_id or "")
    if person:
        notes.append(f"about {_who(person)}")
    notes += _sensitive(fact.sensitivity)
    if fact.certainty is Certainty.UNCERTAIN:
        notes.append("the user wasn't sure")
    suffix = f" ({'; '.join(notes)})" if notes else ""
    return f"- [{fact.id}] {fact.text}{suffix}"


def _by_id(people: Iterable[Person]) -> dict[str, Person]:
    return {person.id: person for person in people}


def render_thread(
    thread: Thread, people: Mapping[str, Person] | None = None, tz: tzinfo | None = None
) -> str:
    """One storyline as a line Claude can update by id."""
    notes = []
    if thread.status is ThreadStatus.SNOOZED and thread.check_in_after:
        notes.append(f"snoozed until {_local_day(thread.check_in_after, tz)}")
    elif thread.status is not ThreadStatus.OPEN:
        notes.append(thread.status.value.replace("_", " "))
    if thread.next_step:
        notes.append(f"next: {thread.next_step}")
    person = (people or {}).get(thread.about_person_id or "")
    if person:
        notes.append(f"about {_who(person)}")
    notes += _sensitive(thread.sensitivity)
    suffix = f" ({'; '.join(notes)})" if notes else ""
    return f"- [{thread.id}] {thread.title}{suffix}"


def _boundary(boundary: Boundary, tz: tzinfo | None) -> str:
    if boundary.scope is BoundaryScope.THIS_CALL:
        scope = "this call only"
    elif boundary.scope is BoundaryScope.UNTIL_DATE:
        scope = f"until {_local_day(boundary.until, tz)}" if boundary.until else "for now"
    else:
        scope = "until they reopen it"
    return f"- [{boundary.id}] {boundary.topic} ({scope})"


def _events(events: Sequence[ThreadEvent], tz: tzinfo | None) -> list[str]:
    return [f"  {_local_day(event.created_at, tz)}: {event.text}" for event in events]


def render_context(context: MemoryContext) -> str:
    """Memory for the agent's instructions at call start: small and chosen."""
    now, tz = context.now, context.now.tzinfo
    people = _by_id(context.people)
    lines = [f"Now: {_time(now)} ({tz})."]

    last = context.last_call
    if last is None:
        lines.append(
            "This is your first call with them. Don't act as if you already know them; get to "
            "know them gently."
        )
    else:
        days = (now.date() - last.started_at.astimezone(tz).date()).days
        line = f"Your last call was {_ago(days)} ({_local_day(last.started_at, tz)})."
        if last.summary:
            line += f" Summary: {last.summary[:MAX_SUMMARY_CHARS]}"
        lines.append(line)
        if last.ended_abruptly and last.unfinished_topic:
            lines.append(
                f'It cut off while they were saying: "{last.unfinished_topic}". Offer once to '
                'continue from there ("Baat yahin ruk gayi thi..."); if they don\'t want to, '
                "let it go."
            )

    if context.boundaries:
        lines += [
            "",
            "Don't bring these up; the user asked (the memories stay). If they reopen one, "
            "call lift_boundary with its id:",
        ]
        lines += [_boundary(boundary, tz) for boundary in context.boundaries]
    if context.declined:
        lines += ["", "Suggestions they didn't like. Never make them again:"]
        lines += [f"- {suggestion.text}" for suggestion in context.declined]
    if context.facts:
        lines += ["", "What the user has asked you to remember (ids in brackets):"]
        lines += [render_memory(fact, people) for fact in context.facts]
    if context.people:
        lines += ["", "People in the user's life: " + ", ".join(_who(p) for p in context.people)]

    check_in = context.check_in
    ongoing = [t for t in context.threads if check_in is None or t.id != check_in.id]
    if ongoing:
        lines += [
            "",
            "Their ongoing storylines. Don't raise these yourself; when the user talks about "
            "one, call update_thread with its id:",
        ]
        lines += [render_thread(thread, people, tz) for thread in ongoing]
    if check_in is not None:
        lines += [
            "",
            "Today's one check-in. Ask about it once, gently and in your own words, and accept "
            '"nahi" or "baad mein". It is never a task. When they tell you how it went, call '
            "update_thread with its id:",
            render_thread(check_in, people, tz),
        ]
        lines += _events(context.check_in_events, tz)

    if not (context.facts or context.people or context.threads):
        lines += ["", "You have nothing saved about this user yet."]
    return "\n".join(lines)


def render_recall(facts: Sequence[ScoredFact]) -> str | None:
    """Hidden per-turn context with memories relevant to what the user just said."""
    if not facts:
        return None
    lines = ["Saved memories that may be relevant to what the user just said:"]
    lines += [render_memory(scored.fact) for scored in facts]
    lines.append("Use them only if they fit; don't list them back.")
    return "\n".join(lines)


def render_search(facts: Sequence[ScoredFact], people: Iterable[Person] = ()) -> str:
    """Tool output for an explicit memory lookup."""
    if not facts:
        return "Nothing saved about that. Say so honestly; don't guess."
    by_id = _by_id(people)
    return "\n".join(render_memory(scored.fact, by_id) for scored in facts)
