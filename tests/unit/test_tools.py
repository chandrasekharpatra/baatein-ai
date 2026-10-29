from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from livekit.agents import llm

from baatein.agent.tools import PAUSED, choice, memory_tools, parse_due_date, user_messages
from baatein.domain.models import Certainty, FollowUpStatus, MemoryKind, Sensitivity
from baatein.memory.service import CallMemory
from tests.factories import make_followup
from tests.fakes.world import World, build_world


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-06", datetime(2026, 11, 6, tzinfo=UTC)),
        ("2026-11-06T15:30:00", datetime(2026, 11, 6, 15, 30, tzinfo=UTC)),
        (
            "2026-11-06T15:30:00+05:30",
            datetime(2026, 11, 6, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        ),
        ("next friday", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_due_date(value: str | None, expected: datetime | None) -> None:
    assert parse_due_date(value) == expected


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
        "add_followup",
        "resolve_followup",
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


async def test_pause_saving_blocks_remember_and_followups() -> None:
    world = build_world()
    _, tools = await tools_for(world)

    assert await tools["pause_saving"]() == "Saving is off for this call."
    assert await tools["remember"]("Likes gardening") == PAUSED
    assert await tools["add_followup"]("Ask about the garden") == PAUSED
    assert world.state.followups == {}


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


async def test_add_followup_saves_with_parsed_date() -> None:
    world = build_world()
    memory, tools = await tools_for(world)

    reply = await tools["add_followup"]("Ask about the exam", "2026-11-06")

    [followup] = world.state.followups.values()
    assert "Saved" in reply
    assert followup.text == "Ask about the exam"
    assert followup.due_at == datetime(2026, 11, 6, tzinfo=UTC)
    assert followup.source_session_id == memory.session.id


async def test_resolve_followup_reports_outcome() -> None:
    world = build_world()
    followup = make_followup(world.user.id)
    world.state.followups[followup.id] = followup
    _, tools = await tools_for(world)

    assert await tools["resolve_followup"](followup.id, "done") == "Updated."
    assert world.state.followups[followup.id].status is FollowUpStatus.DONE
    assert await tools["resolve_followup"](followup.id, "dropped") == (
        "No open follow-up with that id."
    )


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
