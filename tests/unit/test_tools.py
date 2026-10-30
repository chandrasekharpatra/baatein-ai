from datetime import UTC, date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from livekit.agents import llm

from baatein.agent.tools import (
    PAUSED,
    choice,
    honest_promise,
    memory_tools,
    parse_date,
    parse_day,
    parse_time,
    user_messages,
)
from baatein.domain.models import (
    BoundaryScope,
    Certainty,
    MemoryKind,
    Pace,
    Preferences,
    ReminderStatus,
    Repeat,
    Sensitivity,
    ThreadKind,
    ThreadStatus,
)
from baatein.memory.service import CallMemory
from tests.factories import make_reminder, make_thread
from tests.fakes.world import World, build_world

IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-06", datetime(2026, 11, 6, tzinfo=IST)),
        ("2026-11-06T15:30:00", datetime(2026, 11, 6, 15, 30, tzinfo=IST)),
        ("2026-11-06T10:00:00Z", datetime(2026, 11, 6, 10, tzinfo=UTC)),
        (
            "2026-11-06T15:30:00+05:30",
            datetime(2026, 11, 6, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        ),
        ("next friday", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_date_reads_dates_in_the_users_timezone(
    value: str | None, expected: datetime | None
) -> None:
    assert parse_date(value, IST) == expected


def run_context(user_messages: int) -> Any:
    items = [llm.ChatMessage(role="assistant", content=["Namaste"])]
    items += [llm.ChatMessage(role="user", content=[f"msg {i}"]) for i in range(user_messages)]
    return SimpleNamespace(session=SimpleNamespace(history=llm.ChatContext(items)))


def test_user_messages_counts_only_user_chat_messages() -> None:
    assert user_messages(run_context(user_messages=3)) == 3


async def tools_for(world: World) -> tuple[CallMemory, dict[str, Any]]:
    memory = await world.service.open_call(world.user)
    tools = {}
    for tool in memory_tools(memory):
        assert isinstance(tool, llm.FunctionTool)
        tools[tool.info.name] = tool
    return memory, tools


async def test_tools_are_named_for_the_llm() -> None:
    _, tools = await tools_for(build_world())

    assert set(tools) == {
        "recall",
        "remember",
        "correct_memory",
        "forget",
        "pause_saving",
        "rename_person",
        "track_thread",
        "update_thread",
        "forget_thread",
        "set_boundary",
        "lift_boundary",
        "note_declined_suggestion",
        "create_reminder",
        "update_reminder",
        "complete_occurrence",
        "skip_occurrence",
        "cancel_reminder",
        "set_preference",
        "privacy_mode",
    }


async def test_recall_renders_saved_memories_with_people() -> None:
    world = build_world()
    memory, tools = await tools_for(world)
    fact = await memory.remember("Plays cricket with Ravi on Sundays", about="Ravi", relation="son")
    assert fact is not None

    assert await tools["recall"]("cricket") == (
        f"- [{fact.id}] Plays cricket with Ravi on Sundays (about Ravi (son))"
    )
    assert (await tools["recall"]("zebra")).startswith("Nothing saved about that.")


async def test_remember_saves_with_kind_sensitivity_and_uncertainty() -> None:
    world = build_world()
    _, tools = await tools_for(world)

    reply = await tools["remember"](
        "Mother made a special Diwali pulao",
        kind="story",
        quote="Woh masala khud banaati thi",
        about="Maa",
        relation="mother",
        sensitivity="personal",
        uncertain=True,
    )

    [fact] = await world.store.active(world.user.id, limit=5)
    assert reply == f"Saved as [{fact.id}]."
    assert (fact.kind, fact.sensitivity, fact.certainty, fact.quote) == (
        MemoryKind.STORY,
        Sensitivity.PERSONAL,
        Certainty.UNCERTAIN,
        "Woh masala khud banaati thi",
    )


async def test_remember_defaults_to_a_sure_normal_fact() -> None:
    world = build_world()
    _, tools = await tools_for(world)

    await tools["remember"]("Likes masala chai")

    [fact] = await world.store.active(world.user.id, limit=5)
    assert (fact.kind, fact.sensitivity, fact.certainty, fact.about_person_id) == (
        MemoryKind.FACT,
        Sensitivity.NORMAL,
        Certainty.SURE,
        None,
    )


async def test_pause_saving_blocks_memories_and_storylines_but_not_boundaries() -> None:
    world = build_world()
    thread = make_thread(world.user.id)
    world.state.threads[thread.id] = thread
    _, tools = await tools_for(world)

    assert await tools["pause_saving"]() == "Saving is off for this call."
    assert await tools["remember"]("Likes gardening") == PAUSED
    assert await tools["track_thread"]("Grow tomatoes") == PAUSED
    assert await tools["update_thread"](thread.id, "Called him", "done") == PAUSED
    assert list(world.state.threads.values()) == [thread]
    assert await tools["set_boundary"]("garden") == "Done. Don't bring it up."
    assert len(world.state.boundaries) == 1


async def test_correct_memory_and_forget_report_outcomes() -> None:
    world = build_world()
    memory, tools = await tools_for(world)
    fact = await memory.remember("Suman is my sister")
    assert fact is not None

    corrected = await tools["correct_memory"](fact.id, "Sunita is my neighbour")
    [current] = await world.store.active(world.user.id, limit=5)

    assert corrected == f"Corrected as [{current.id}]."
    assert await tools["correct_memory"](fact.id, "x") == "No current memory with that id."
    asked = await tools["forget"](run_context(user_messages=1), current.id)
    assert asked.startswith("Not deleted yet. Ask the user")
    assert await world.store.active(world.user.id, limit=5) == [current]
    assert await tools["forget"](run_context(user_messages=2), current.id) == "Deleted."
    gone = await tools["forget"](run_context(user_messages=3), current.id)
    assert gone == "No saved memory with that id."
    assert await world.store.active(world.user.id, limit=5) == []


async def test_rename_person_reports_outcome() -> None:
    world = build_world()
    memory, tools = await tools_for(world)
    await memory.person("Suman")

    assert await tools["rename_person"]("Suman", "Sunita") == "Renamed."
    assert await tools["rename_person"]("Suman", "X") == "No saved person with that name."


async def test_track_thread_saves_storyline_with_local_check_in_date() -> None:
    world = build_world()
    memory, tools = await tools_for(world)

    reply = await tools["track_thread"](
        "Reconnect with Ramesh",
        next_step="Call him",
        check_in_after="2026-11-02",
        kind="relationship",
        about="Ramesh",
        relation="college friend",
        sensitivity="personal",
    )

    [thread] = world.state.threads.values()
    assert reply == f"Tracking as [{thread.id}]."
    assert (thread.title, thread.next_step, thread.kind) == (
        "Reconnect with Ramesh",
        "Call him",
        ThreadKind.RELATIONSHIP,
    )
    assert thread.check_in_after == datetime(2026, 11, 2, tzinfo=IST)
    assert thread.sensitivity is Sensitivity.PERSONAL
    assert thread.source_session_id == memory.session.id
    [ramesh] = world.state.people.values()
    assert (thread.about_person_id, ramesh.relation) == (ramesh.id, "college friend")


async def test_track_thread_defaults_to_an_open_plan() -> None:
    world = build_world()
    _, tools = await tools_for(world)

    await tools["track_thread"]("Sing for granddaughter", kind="hobby")

    [thread] = world.state.threads.values()
    assert (thread.kind, thread.status, thread.sensitivity) == (
        ThreadKind.PLAN,
        ThreadStatus.OPEN,
        Sensitivity.NORMAL,
    )
    assert thread.check_in_after is None


async def test_update_thread_records_event_and_status() -> None:
    world = build_world()
    thread = make_thread(world.user.id)
    world.state.threads[thread.id] = thread
    _, tools = await tools_for(world)

    assert await tools["update_thread"](thread.id, "Called Ramesh", "done") == "Updated."
    assert await tools["update_thread"](thread.id, status="sideways") == "Updated."
    assert await tools["update_thread"]("missing", "x") == "No storyline with that id."

    assert world.state.threads[thread.id].status is ThreadStatus.DONE
    assert [(e.text, e.status) for e in world.state.thread_events] == [
        ("Called Ramesh", ThreadStatus.DONE)
    ]


async def test_set_and_lift_boundary() -> None:
    world = build_world()
    _, tools = await tools_for(world)

    await tools["set_boundary"]("painting", "until_date", "2026-11-06")
    [boundary] = world.state.boundaries.values()

    assert (boundary.topic, boundary.scope) == ("painting", BoundaryScope.UNTIL_DATE)
    assert boundary.until == datetime(2026, 11, 6, tzinfo=IST)
    assert await tools["lift_boundary"](boundary.id) == "Lifted."
    assert await tools["lift_boundary"](boundary.id) == "No active boundary with that id."


async def test_set_boundary_defaults_to_until_reopened() -> None:
    world = build_world()
    _, tools = await tools_for(world)

    await tools["set_boundary"]("almari")

    [boundary] = world.state.boundaries.values()
    assert boundary.scope is BoundaryScope.UNTIL_REOPENED


async def test_note_declined_suggestion_and_privacy_mode() -> None:
    world = build_world()
    memory, tools = await tools_for(world)

    assert "Never suggest it again" in await tools["note_declined_suggestion"]("go for a walk")
    assert "Privacy mode is on" in await tools["privacy_mode"]()

    assert [s.text for s in world.state.suggestions] == ["go for a walk"]
    assert memory.private


def _schemas_with_enums(node: object) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        found = [node] if "enum" in node else []
        return found + [s for value in node.values() for s in _schemas_with_enums(value)]
    if isinstance(node, list):
        return [s for value in node for s in _schemas_with_enums(value)]
    return []


async def test_strict_anthropic_schemas_keep_enums_consistent_with_nullable_types() -> None:
    _, tools = await tools_for(build_world())
    schemas = llm.ToolContext(list(tools.values())).parse_function_tools("anthropic", strict=True)

    for schema in _schemas_with_enums(schemas):
        types = schema.get("type")
        if isinstance(types, list) and "null" in types:
            assert None in schema["enum"], schema


def test_choice_falls_back_to_default_for_missing_or_unknown_values() -> None:
    assert choice(MemoryKind, "story", MemoryKind.FACT) is MemoryKind.STORY
    assert choice(MemoryKind, None, MemoryKind.FACT) is MemoryKind.FACT
    assert choice(MemoryKind, "anecdote", MemoryKind.FACT) is MemoryKind.FACT
    assert choice(ThreadStatus, None, None) is None


async def test_forget_thread_reports_outcomes() -> None:
    world = build_world()
    thread = make_thread(world.user.id)
    world.state.threads[thread.id] = thread
    _, tools = await tools_for(world)

    asked = await tools["forget_thread"](run_context(user_messages=1), thread.id)
    assert "call forget_thread again" in asked
    assert await tools["forget_thread"](run_context(user_messages=2), thread.id) == "Deleted."
    gone = await tools["forget_thread"](run_context(user_messages=3), thread.id)
    assert gone == "No storyline with that id."


async def test_set_preference_saves_and_applies_right_away() -> None:
    world = build_world()
    memory = await world.service.open_call(world.user)
    applied: list[Preferences] = []

    async def on_preferences(preferences: Preferences) -> None:
        applied.append(preferences)

    tools = {t.info.name: t for t in memory_tools(memory, on_preferences)}  # type: ignore[union-attr]

    reply = await tools["set_preference"](pace="slow", fewer_questions=True, verbosity="chatty")

    expected = Preferences(pace=Pace.SLOW, fewer_questions=True)
    assert reply.startswith("Done.")
    assert applied == [expected]
    assert world.state.users[world.user.id].preferences == expected
    _, plain = await tools_for(world)
    assert (await plain["set_preference"](address_as="Mohan ji")).startswith("Done.")


def test_parse_day_and_time() -> None:
    assert parse_day("2026-11-18") == date(2026, 11, 18)
    assert parse_day("18 Nov") is None
    assert parse_day(None) is None
    assert parse_time("09:30") == time(9, 30)
    assert parse_time("9 baje") is None
    assert parse_time("") is None


async def test_reminder_tools_create_update_settle_and_cancel() -> None:
    world = build_world()
    _, tools = await tools_for(world)

    saved = await tools["create_reminder"](
        "Ravi's birthday", "2026-11-18", "09:00", "yearly", [7, 0], "Ravi", "Buy a gift"
    )
    waiting = await tools["create_reminder"]("June trip", repeat="sometimes")
    birthday, trip = world.state.reminders.values()

    assert saved.startswith(f"Saved as [{birthday.id}]. You can't alert them on a day")
    assert '"Jab bhi hum 11 November se 18 November ke beech baat karenge' in saved
    assert '"11 November ko yaad dilaungi"' in saved
    assert saved.endswith(
        "They asked for 09:00: say plainly that you can't remind them at a set "
        "time, and don't say it is saved for that time."
    )
    assert "They asked for" not in await tools["create_reminder"]("Dentist", "2026-11-02")
    assert (birthday.on, birthday.repeat, birthday.alert_days_before) == (
        date(2026, 11, 18),
        Repeat.YEARLY,
        (7, 0),
    )
    assert (
        waiting == f"Saved as [{trip.id}], waiting for a date. Ask for it only when they know it."
    )
    assert trip.repeat is Repeat.ONCE

    assert await tools["update_reminder"](trip.id, date="2026-06-22", days_before=[1]) == "Updated."
    assert world.state.reminders[trip.id].alert_days_before == (1,)
    assert await tools["update_reminder"]("missing") == "No open reminder with that id."

    done = await tools["complete_occurrence"](trip.id)
    assert done == "Marked done for Mon 22 Jun 2026."
    assert world.state.reminders[trip.id].status is ReminderStatus.DONE
    skipped = await tools["skip_occurrence"](birthday.id, "2026-11-18")
    assert skipped == "Marked skipped for Wed 18 Nov 2026."
    assert (await tools["skip_occurrence"]("missing")).startswith("No open reminder")

    asked = await tools["cancel_reminder"](run_context(user_messages=1), birthday.id)
    assert "call cancel_reminder again" in asked
    assert await tools["cancel_reminder"](run_context(user_messages=2), birthday.id) == (
        "Cancelled."
    )
    assert await tools["cancel_reminder"](run_context(user_messages=3), birthday.id) == (
        "No open reminder with that id."
    )


def test_the_honest_promise_covers_the_days_baatein_will_bring_it_up() -> None:
    on_the_day = make_reminder("u", on=date(2026, 3, 1))
    week_before = make_reminder("u", on=date(2026, 3, 1), alert_days_before=(0, 7))

    assert honest_promise(on_the_day) == (
        "Jab bhi hum 1 March ke aas-paas baat karenge, main yaad dila dungi."
    )
    assert honest_promise(week_before) == (
        "Jab bhi hum 22 February se 1 March ke beech baat karenge, main yaad dila dungi."
    )
