from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from livekit.agents import RunContext, function_tool, llm

from baatein.domain.models import Certainty, FollowUpStatus, MemoryKind, Sensitivity
from baatein.memory.context_builder import render_search
from baatein.memory.service import CallMemory, ForgetOutcome

PAUSED = "Not saved: the user asked you not to save anything in this call."
FORGET_REPLIES = {
    ForgetOutcome.DELETED: "Deleted.",
    ForgetOutcome.NEEDS_CONFIRMATION: (
        'Not deleted yet. Ask the user to confirm, e.g. "Pakka, main yeh poori tarah mita '
        'doon?" If they say yes, call forget again with the same id.'
    ),
    ForgetOutcome.NOT_FOUND: "No saved memory with that id.",
}


def parse_due_date(value: str | None) -> datetime | None:
    """`YYYY-MM-DD` (or a full ISO datetime) → aware UTC datetime; None if missing or invalid."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def user_messages(context: RunContext[None]) -> int:
    """How many messages the user has sent so far in this call."""
    return sum(
        1
        for item in context.session.history.items
        if isinstance(item, llm.ChatMessage) and item.role == "user"
    )


def choice[E: StrEnum](enum: type[E], value: str | None, default: E) -> E:
    """The enum member named by `value`, or `default` if it is missing or unknown.

    Optional tool parameters are plain strings: Anthropic rejects an enum on a nullable type.
    """
    try:
        return enum(value) if value else default
    except ValueError:
        return default


def memory_tools(memory: CallMemory) -> list[llm.Tool | llm.Toolset]:
    @function_tool
    async def recall(query: str) -> str:
        """Look up what the user has asked you to remember, when they refer to something from
        before that isn't already in your context. If nothing is found, say so honestly.

        Args:
            query: What to look for, e.g. "Ramesh" or "sister's wedding".
        """
        facts = await memory.search(query)
        return render_search(facts, await memory.people() if facts else [])

    @function_tool
    async def remember(
        text: str,
        kind: str | None = None,
        quote: str | None = None,
        about: str | None = None,
        relation: str | None = None,
        sensitivity: str | None = None,
        uncertain: bool | None = None,
    ) -> str:
        """Save something about the user. Call it when the user asks you to remember something,
        or after you asked "Kya main yeh yaad rakhun?" and they clearly said yes. Never save
        OTPs, PINs, passwords or account numbers.

        Args:
            text: The memory in one short English sentence, e.g. "Misses college friend Ramesh
                and wants to call him again".
            kind: One of "fact" (default), "story" for a personal story kept as told,
                "preference" for likes and dislikes, "moment" for a happy event.
            quote: The user's own key words, if worth keeping, e.g. "Hum har shaam saath chai
                peete the".
            about: Name of another person this is mainly about, e.g. "Ramesh".
            relation: That person's relation to the user, e.g. "college friend", "son".
            sensitivity: One of "normal" (default), "personal" for private matters, "health",
                "grief" or "crisis" for those topics.
            uncertain: True if the user wasn't sure about the details.
        """
        fact = await memory.remember(
            text,
            kind=choice(MemoryKind, kind, MemoryKind.FACT),
            quote=quote,
            about=about,
            relation=relation,
            sensitivity=choice(Sensitivity, sensitivity, Sensitivity.NORMAL),
            certainty=Certainty.UNCERTAIN if uncertain else Certainty.SURE,
        )
        return PAUSED if fact is None else f"Saved as [{fact.id}]."

    @function_tool
    async def correct_memory(memory_id: str, text: str) -> str:
        """Replace a saved memory the user says is wrong or has changed, e.g. "Suman meri behen
        nahi, padosan hai". Apologise simply; don't argue from what was saved.

        Args:
            memory_id: The id in brackets next to the memory.
            text: The corrected memory in one short English sentence.
        """
        fact = await memory.correct(memory_id, text)
        return "No current memory with that id." if fact is None else f"Corrected as [{fact.id}]."

    @function_tool
    async def forget(context: RunContext[None], memory_id: str) -> str:
        """Delete a saved memory completely when the user asks you to forget it. The first call
        only asks for confirmation; call it again with the same id after the user confirms.
        Only tell the user it is deleted if this returns "Deleted".

        Args:
            memory_id: The id in brackets next to the memory.
        """
        return FORGET_REPLIES[await memory.forget(memory_id, user_messages(context))]

    @function_tool
    async def pause_saving() -> str:
        """Stop saving anything for the rest of this call, when the user says not to save
        ("save mat karna"). You can still talk about the topic."""
        memory.pause_saving()
        return "Saving is off for this call."

    @function_tool
    async def rename_person(name: str, new_name: str) -> str:
        """Fix the name of a person you have saved, e.g. "naam Sunita hai, Suman nahi".

        Args:
            name: The name as saved.
            new_name: The correct name.
        """
        person = await memory.rename_person(name, new_name)
        return "No saved person with that name." if person is None else "Renamed."

    @function_tool
    async def add_followup(text: str, due_date: str | None = None) -> str:
        """Plan to ask the user about something in a later call, such as an upcoming
        interview, trip or appointment. Only after the user agreed you can check in on it.

        Args:
            text: What to ask about, in English, e.g. "Ask how the Google interview went".
            due_date: When to ask, as YYYY-MM-DD, if the user mentioned a date.
        """
        if memory.saving_paused:
            return PAUSED
        await memory.add_followup(text, parse_due_date(due_date))
        return "Saved."

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

    return [
        recall,
        remember,
        correct_memory,
        forget,
        pause_saving,
        rename_person,
        add_followup,
        resolve_followup,
    ]
