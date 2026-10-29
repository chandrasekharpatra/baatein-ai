from datetime import UTC, datetime
from typing import Literal

from livekit.agents import function_tool, llm

from baatein.domain.models import FollowUpStatus
from baatein.memory.context_builder import render_search
from baatein.memory.service import CallMemory


def parse_due_date(value: str | None) -> datetime | None:
    """`YYYY-MM-DD` (or a full ISO datetime) → aware UTC datetime; None if missing or invalid."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def memory_tools(memory: CallMemory) -> list[llm.Tool | llm.Toolset]:
    @function_tool
    async def recall_memory(query: str) -> str:
        """Search what you remember about the user from earlier calls. Use it when the user
        refers to something from before that isn't already in your context.

        Args:
            query: What to look for, e.g. "sister's wedding" or "job interview".
        """
        return render_search(await memory.search(query))

    @function_tool
    async def add_followup(text: str, due_date: str | None = None) -> str:
        """Remember to ask the user about something in a later call, such as an upcoming
        interview, exam, trip or appointment.

        Args:
            text: What to ask about, in English, e.g. "Ask how the Google interview went".
            due_date: When to ask, as YYYY-MM-DD, if the user mentioned a date.
        """
        await memory.add_followup(text, parse_due_date(due_date))
        return "Saved. Don't mention saving it to the user."

    @function_tool
    async def resolve_followup(followup_id: str, outcome: Literal["done", "dropped"]) -> str:
        """Mark a follow-up from your context as handled once the user has answered it
        ("done"), or as no longer relevant ("dropped").

        Args:
            followup_id: The id shown in brackets next to the follow-up.
            outcome: "done" or "dropped".
        """
        resolved = await memory.resolve_followup(followup_id, FollowUpStatus(outcome))
        return "Updated." if resolved else "No open follow-up with that id."

    return [recall_memory, add_followup, resolve_followup]
