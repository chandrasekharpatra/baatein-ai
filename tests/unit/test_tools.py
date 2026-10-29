from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from livekit.agents import llm

from baatein.agent.tools import memory_tools, parse_due_date
from baatein.domain.models import FollowUpStatus
from baatein.memory.service import CallMemory
from tests.factories import make_fact, make_followup
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


async def tools_for(world: World) -> tuple[CallMemory, dict[str, Any]]:
    memory = await world.service.open_call(world.user)
    tools = {}
    for tool in memory_tools(memory):
        assert isinstance(tool, llm.FunctionTool)
        tools[tool.info.name] = tool
    return memory, tools


async def test_tools_are_named_for_the_llm() -> None:
    _, tools = await tools_for(build_world())

    assert set(tools) == {"recall_memory", "add_followup", "resolve_followup"}


async def test_recall_memory_renders_search_results() -> None:
    world = build_world()
    await world.store.add(make_fact(world.user.id, "Plays cricket on Sundays"))
    _, tools = await tools_for(world)

    assert "- Plays cricket on Sundays" in await tools["recall_memory"]("cricket")
    assert await tools["recall_memory"]("zebra") == "Nothing found in memory."


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
