from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta, tzinfo
from enum import StrEnum

from livekit.agents import RunContext, function_tool, llm

from baatein.domain.models import (
    BoundaryScope,
    Certainty,
    MemoryKind,
    OccurrenceStatus,
    Pace,
    Preferences,
    Reminder,
    Repeat,
    Sensitivity,
    ThreadKind,
    ThreadStatus,
    Verbosity,
)
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
CANCEL_REPLIES = {
    ForgetOutcome.DELETED: "Cancelled.",
    ForgetOutcome.NEEDS_CONFIRMATION: (
        'Not cancelled yet. Ask the user to confirm, e.g. "Pakka, yeh reminder band kar doon?" '
        "If they say yes, call cancel_reminder again with the same id."
    ),
    ForgetOutcome.NOT_FOUND: "No open reminder with that id.",
}
REMINDER_SAVED = (
    "Saved as [{id}]. You can't alert them on a day or at a time, only bring it up when they "
    'talk to you. Tell them in these words: "{promise}" Never promise a day or a time '
    '("{first} ko yaad dilaungi", "9 baje yaad dila dungi").'
)


def honest_promise(reminder: Reminder) -> str:
    """What Baatein can truly promise for a dated reminder: to bring it up in any call from its
    earliest alert to the day itself (decision 3)."""
    assert reminder.on is not None
    day = f"{reminder.on.day} {reminder.on:%B}"
    lead = max(reminder.alert_days_before, default=0)
    if lead == 0:
        return f"Jab bhi hum {day} ke aas-paas baat karenge, main yaad dila dungi."
    first = reminder.on - timedelta(days=lead)
    start = f"{first.day} {first:%B}"
    return f"Jab bhi hum {start} se {day} ke beech baat karenge, main yaad dila dungi."


NEEDS_DATE = "Saved as [{id}], waiting for a date. Ask for it only when they know it."
FORGET_THREAD_REPLIES = FORGET_REPLIES | {
    ForgetOutcome.NEEDS_CONFIRMATION: FORGET_REPLIES[ForgetOutcome.NEEDS_CONFIRMATION].replace(
        "call forget again", "call forget_thread again"
    ),
    ForgetOutcome.NOT_FOUND: "No storyline with that id.",
}


def parse_date(value: str | None, tz: tzinfo) -> datetime | None:
    """`YYYY-MM-DD` (or an ISO datetime) in the user's timezone; None if missing or invalid."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)


def parse_day(value: str | None) -> date | None:
    """`YYYY-MM-DD` → date; None if missing or invalid."""
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def parse_time(value: str | None) -> time | None:
    """`HH:MM` (24-hour) → time; None if missing or invalid."""
    try:
        return time.fromisoformat(value) if value else None
    except ValueError:
        return None


def user_messages(context: RunContext[None]) -> int:
    """How many messages the user has sent so far in this call."""
    return sum(
        1
        for item in context.session.history.items
        if isinstance(item, llm.ChatMessage) and item.role == "user"
    )


def choice[E: StrEnum, D](enum: type[E], value: str | None, default: D) -> E | D:
    """The enum member named by `value`, or `default` if it is missing or unknown.

    Optional choices are plain strings: Anthropic's strict schemas reject an enum on a nullable
    type, and Claude may still send an unknown value.
    """
    try:
        return enum(value) if value else default
    except ValueError:
        return default


def memory_tools(
    memory: CallMemory,
    on_preferences: Callable[[Preferences], Awaitable[None]] | None = None,
) -> list[llm.Tool | llm.Toolset]:
    @function_tool
    async def recall(query: str) -> str:
        """Look up what the user has asked you to remember. Call it whenever they ask about or
        refer to something from before that isn't in your context, before ever saying you don't
        have it saved. If nothing is found, say so honestly.

        Args:
            query: A few English keywords, e.g. "Ramesh", "wedding Sabitri", "father job".
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
            sensitivity: One of "normal" (default), "personal" for private matters they
                wouldn't want raised (money troubles, family disputes, secrets), "health",
                "grief" or "crisis" for those topics. Everyday feelings, like missing a friend,
                are "normal".
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
    async def track_thread(
        title: str,
        next_step: str | None = None,
        check_in_after: str | None = None,
        kind: str | None = None,
        about: str | None = None,
        relation: str | None = None,
        sensitivity: str | None = None,
    ) -> str:
        """Start following a storyline in the user's life (a plan, a wish, a worry, a person
        they want to reconnect with), so you can gently ask about it in a later call. Only after
        the user agreed you can check in on it.

        Args:
            title: The storyline in a few English words, e.g. "Reconnect with Ramesh".
            next_step: What the user plans to do next, e.g. "Call him on Sunday".
            check_in_after: The first day to ask about it, as YYYY-MM-DD, e.g. the day after
                a planned Sunday call. Leave out to ask in any later call.
            kind: One of "plan" (default), "relationship", "aspiration", "intention",
                "worry", "story".
            about: Name of the person it is mainly about, e.g. "Ramesh".
            relation: That person's relation to the user, e.g. "college friend".
            sensitivity: As for remember: "normal" (default) unless it is private ("personal"),
                "health", "grief" or "crisis". A sensitive storyline is never a check-in.
        """
        thread = await memory.track_thread(
            title,
            kind=choice(ThreadKind, kind, ThreadKind.PLAN),
            next_step=next_step,
            check_in_after=parse_date(check_in_after, memory.timezone),
            about=about,
            relation=relation,
            sensitivity=choice(Sensitivity, sensitivity, Sensitivity.NORMAL),
        )
        return PAUSED if thread is None else f"Tracking as [{thread.id}]."

    @function_tool
    async def update_thread(
        thread_id: str,
        event: str | None = None,
        status: str | None = None,
        next_step: str | None = None,
        check_in_after: str | None = None,
    ) -> str:
        """Record what happened in one of the user's storylines, e.g. "Maine unko phone kar
        diya!". Not doing something, or changing their mind, is fine; never make them feel
        guilty.

        Args:
            thread_id: The id in brackets next to the storyline.
            event: What happened, in one short English sentence, e.g. "Called Ramesh".
            status: One of "in_progress", "done", "not_done", "cancelled" (they no longer want
                it), "snoozed" (not now; set check_in_after), or "open".
            next_step: What they plan to do next, if anything.
            check_in_after: The first day to ask again, as YYYY-MM-DD.
        """
        if memory.saving_paused:
            return PAUSED
        thread = await memory.update_thread(
            thread_id,
            event=event,
            status=choice(ThreadStatus, status, None),
            next_step=next_step,
            check_in_after=parse_date(check_in_after, memory.timezone),
        )
        return "No storyline with that id." if thread is None else "Updated."

    @function_tool
    async def forget_thread(context: RunContext[None], thread_id: str) -> str:
        """Delete a storyline completely when the user asks you to forget it, e.g. "Ramesh wali
        baat bhool jao". The first call only asks for confirmation; call it again with the same
        id after the user confirms. If they only want you to stop asking, use set_boundary.

        Args:
            thread_id: The id in brackets next to the storyline.
        """
        return FORGET_THREAD_REPLIES[await memory.forget_thread(thread_id, user_messages(context))]

    @function_tool
    async def set_boundary(
        topic: str,
        scope: str | None = None,
        until: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """Stop bringing a topic up because the user asked, e.g. "aaj painting yaad nahi
        dilana" or "almari ke baare mein mat poochhna". Nothing is deleted.

        Args:
            topic: The topic in a few English words, e.g. "painting".
            scope: "this_call" for today only, "until_reopened" (default) until the user brings
                it up again, or "until_date" with until.
            until: The day it may come up again, as YYYY-MM-DD, for "until_date".
            thread_id: The storyline's id in brackets, if the topic is one of them.
        """
        await memory.set_boundary(
            topic,
            choice(BoundaryScope, scope, BoundaryScope.UNTIL_REOPENED),
            parse_date(until, memory.timezone),
            thread_id,
        )
        return "Done. Don't bring it up."

    @function_tool
    async def lift_boundary(boundary_id: str) -> str:
        """The user reopened a topic they had asked you not to bring up, e.g. "ab painting ke
        baare mein poochh sakte ho".

        Args:
            boundary_id: The id in brackets next to the boundary.
        """
        lifted = await memory.lift_boundary(boundary_id)
        return "Lifted." if lifted else "No active boundary with that id."

    @function_tool
    async def note_declined_suggestion(suggestion: str) -> str:
        """The user didn't like a suggestion you made, e.g. "tumne walk ki baat ki thi, main
        naraz tha". Apologise simply; it won't be suggested again.

        Args:
            suggestion: The suggestion in a few English words, e.g. "go for a walk".
        """
        await memory.note_declined(suggestion)
        return "Noted. Never suggest it again."

    @function_tool
    async def create_reminder(
        title: str,
        date: str | None = None,
        time: str | None = None,
        repeat: str | None = None,
        days_before: list[int] | None = None,
        about: str | None = None,
        note: str | None = None,
    ) -> str:
        """Save a reminder the user asked for, e.g. "Ravi ka birthday 18 November, har saal
        yaad dilana". First read back the date and repeat (and a time only if they gave one) in
        simple words and wait for "haan". Never guess a date: if they aren't sure, leave it out.

        Args:
            title: What to remind them of, in a few English words, e.g. "Ravi's birthday".
            date: The (first) day of the event as YYYY-MM-DD, resolved from "agle shanivaar",
                "parson" etc. using today's date.
            time: Only if they gave one, as HH:MM (24-hour), e.g. "18:30" for a medicine.
            repeat: "once" (default), "weekly", "monthly_date" (same date every month),
                "monthly_weekday" (e.g. first Sunday of every month), or "yearly".
            days_before: Days before the event to remind them too, e.g. [7, 0] for a week
                before and on the day. Default [0].
            about: Name of the person it is about, e.g. "Ravi".
            note: Anything else they said, e.g. "Buy a gift".
        """
        reminder = await memory.create_reminder(
            title,
            on=parse_day(date),
            at=parse_time(time),
            repeat=choice(Repeat, repeat, Repeat.ONCE),
            alert_days_before=tuple(days_before or (0,)),
            about=about,
            note=note,
        )
        if reminder.on is None:
            return NEEDS_DATE.format(id=reminder.id)
        first = reminder.on - timedelta(days=max(reminder.alert_days_before, default=0))
        reply = REMINDER_SAVED.format(
            id=reminder.id, promise=honest_promise(reminder), first=f"{first.day} {first:%B}"
        )
        if time is not None:
            reply += (
                f" They asked for {time}: say plainly that you can't remind them at a set time, "
                "and don't say it is saved for that time."
            )
        return reply

    @function_tool
    async def update_reminder(
        reminder_id: str,
        title: str | None = None,
        date: str | None = None,
        time: str | None = None,
        repeat: str | None = None,
        days_before: list[int] | None = None,
        note: str | None = None,
    ) -> str:
        """Change a reminder in place, e.g. "somvaar ko phir yaad dilao" or "purani ko hi badal
        do". Never create a second reminder for the same thing. Read the change back first.

        Args:
            reminder_id: The id in brackets next to the reminder.
            title: New title, if it changed.
            date: New day as YYYY-MM-DD.
            time: New time as HH:MM (24-hour).
            repeat: "once", "weekly", "monthly_date", "monthly_weekday" or "yearly".
            days_before: New list of days before the event to remind them.
            note: New note.
        """
        reminder = await memory.update_reminder(
            reminder_id,
            title=title,
            on=parse_day(date),
            at=parse_time(time),
            repeat=choice(Repeat, repeat, None),
            alert_days_before=tuple(days_before) if days_before else None,
            note=note,
        )
        return "No open reminder with that id." if reminder is None else "Updated."

    async def settle(reminder_id: str, status: OccurrenceStatus, on: str | None) -> str:
        day = await memory.settle_occurrence(reminder_id, status, parse_day(on))
        if day is None:
            return "No open reminder with that id, or no occurrence near today."
        return f"Marked {status.value} for {day:%a} {day.day} {day:%b %Y}."

    @function_tool
    async def complete_occurrence(reminder_id: str, date: str | None = None) -> str:
        """The user has done what this occurrence was about, e.g. "bill bhar diya". Its
        remaining alerts stop; a repeating reminder continues next time.

        Args:
            reminder_id: The id in brackets next to the reminder.
            date: Which occurrence, as YYYY-MM-DD. Default: the one nearest today.
        """
        return await settle(reminder_id, OccurrenceStatus.DONE, date)

    @function_tool
    async def skip_occurrence(reminder_id: str, date: str | None = None) -> str:
        """Skip one occurrence of a repeating reminder, e.g. "sirf is baar skip kar do". The
        series continues.

        Args:
            reminder_id: The id in brackets next to the reminder.
            date: Which occurrence, as YYYY-MM-DD. Default: the one nearest today.
        """
        return await settle(reminder_id, OccurrenceStatus.SKIPPED, date)

    @function_tool
    async def cancel_reminder(context: RunContext[None], reminder_id: str) -> str:
        """Stop a reminder for good when the user asks. The first call only asks for
        confirmation; call it again with the same id after the user confirms.

        Args:
            reminder_id: The id in brackets next to the reminder.
        """
        return CANCEL_REPLIES[await memory.cancel_reminder(reminder_id, user_messages(context))]

    @function_tool
    async def set_preference(
        address_as: str | None = None,
        pace: str | None = None,
        verbosity: str | None = None,
        fewer_questions: bool | None = None,
    ) -> str:
        """Change how you talk to the user when they ask, e.g. "dheere bolo" (pace "slow"),
        "itne sawaal mat poochho" (fewer_questions), "chhota jawab do" (verbosity "short") or
        "mujhe Mohan kaho" (address_as). It applies now and in later calls. Likes and dislikes
        about other things ("chai mein elaichi nahi") are memories, not preferences.

        Args:
            address_as: How they want to be addressed, e.g. "Mohan ji".
            pace: "slow", "normal" or "fast" speech.
            verbosity: "short", "normal" or "detailed" replies.
            fewer_questions: True if they want fewer questions, False to undo that.
        """
        preferences = await memory.set_preferences(
            address_as=address_as,
            pace=choice(Pace, pace, None),
            verbosity=choice(Verbosity, verbosity, None),
            fewer_questions=fewer_questions,
        )
        if on_preferences is not None:
            await on_preferences(preferences)
        return "Done. From now on, talk to them this way."

    @function_tool
    async def privacy_mode() -> str:
        """Others can hear the user, e.g. "kamre mein mehmaan hain". For the rest of this call,
        don't say anything personal aloud."""
        memory.enable_privacy_mode()
        return (
            "Privacy mode is on for this call: don't mention names, family, health, money or "
            "anything you remember about them. Keep it light and general."
        )

    return [
        recall,
        remember,
        correct_memory,
        forget,
        pause_saving,
        rename_person,
        track_thread,
        update_thread,
        forget_thread,
        set_boundary,
        lift_boundary,
        note_declined_suggestion,
        create_reminder,
        update_reminder,
        complete_occurrence,
        skip_occurrence,
        cancel_reminder,
        set_preference,
        privacy_mode,
    ]
