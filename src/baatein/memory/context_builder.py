from collections.abc import Iterable, Mapping, Sequence
from datetime import date

from baatein.domain.models import Certainty, Fact, MemoryKind, Person, ScoredFact, Sensitivity
from baatein.memory.service import MemoryContext

MAX_SUMMARY_CHARS = 400


def _day(value: date) -> str:
    return f"{value:%a} {value.day} {value:%b %Y}"


def _who(person: Person) -> str:
    return f"{person.name} ({person.relation})" if person.relation else person.name


def render_memory(fact: Fact, people: Mapping[str, Person] | None = None) -> str:
    """One memory as a line Claude can refer back to by id."""
    notes = []
    if fact.kind is not MemoryKind.FACT:
        notes.append(fact.kind.value)
    person = (people or {}).get(fact.about_person_id or "")
    if person:
        notes.append(f"about {_who(person)}")
    if fact.sensitivity is not Sensitivity.NORMAL:
        notes.append(f"{fact.sensitivity.value}: only if the user brings it up")
    if fact.certainty is Certainty.UNCERTAIN:
        notes.append("the user wasn't sure")
    suffix = f" ({'; '.join(notes)})" if notes else ""
    return f"- [{fact.id}] {fact.text}{suffix}"


def _by_id(people: Iterable[Person]) -> dict[str, Person]:
    return {person.id: person for person in people}


def render_context(context: MemoryContext) -> str:
    """Memory for the agent's instructions at call start."""
    people = _by_id(context.people)
    lines = [f"Today is {_day(context.today)}."]
    if context.facts:
        lines += ["", "What the user has asked you to remember (ids in brackets):"]
        lines += [render_memory(fact, people) for fact in context.facts]
    if context.people:
        lines += ["", "People in the user's life: " + ", ".join(_who(p) for p in context.people)]
    if context.summaries:
        lines += ["", "Your recent calls with them:"]
        for session in context.summaries:
            summary = (session.summary or "")[:MAX_SUMMARY_CHARS]
            lines.append(f"- {_day(session.started_at.date())}: {summary}")
    if context.followups:
        lines += [
            "",
            "Things to follow up on. Bring up the most relevant one naturally, and call "
            "resolve_followup with its id once the user has answered:",
        ]
        for followup in context.followups:
            due = f" (due {_day(followup.due_at.date())})" if followup.due_at else ""
            lines.append(f"- [{followup.id}] {followup.text}{due}")
    if not (context.facts or context.people or context.summaries or context.followups):
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
