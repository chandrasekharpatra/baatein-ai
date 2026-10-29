from collections.abc import Sequence
from datetime import date

from baatein.domain.models import Role, ScoredFact
from baatein.memory.service import MemoryContext, SearchResult

MAX_SUMMARY_CHARS = 400


def _day(value: date) -> str:
    return f"{value:%a} {value.day} {value:%b %Y}"


def render_context(context: MemoryContext) -> str:
    """Memory for the agent's instructions at call start."""
    lines = [f"Today is {_day(context.today)}."]
    if context.facts:
        lines += ["", "What you know about the user:"]
        lines += [f"- {fact.text}" for fact in context.facts]
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
    if not (context.facts or context.summaries or context.followups):
        lines += ["", "You don't remember anything about this user yet."]
    return "\n".join(lines)


def render_recall(facts: Sequence[ScoredFact]) -> str | None:
    """Hidden per-turn context with facts relevant to what the user just said."""
    if not facts:
        return None
    lines = ["Things you remember that may be relevant to what the user just said:"]
    lines += [f"- {scored.fact.text}" for scored in facts]
    lines.append("Use them only if they fit; don't list them back.")
    return "\n".join(lines)


def render_search(result: SearchResult) -> str:
    """Tool output for an explicit memory lookup."""
    if not (result.facts or result.turns):
        return "Nothing found in memory."
    lines: list[str] = []
    if result.facts:
        lines.append("Facts:")
        lines += [f"- {scored.fact.text}" for scored in result.facts]
    if result.turns:
        lines.append("From earlier calls:")
        for turn in result.turns:
            speaker = "User" if turn.role is Role.USER else "You"
            lines.append(f"- {_day(turn.created_at.date())}, {speaker}: {turn.text}")
    return "\n".join(lines)
