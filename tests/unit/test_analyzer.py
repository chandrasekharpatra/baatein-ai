from types import SimpleNamespace
from typing import Any

import pytest

from baatein.domain.models import CallSummary, Role
from baatein.memory.analyzer import Analysis, ClaudeAnalyzer, render_call
from baatein.ports.analyzer import CallRecord
from tests.factories import make_turn

CALL = CallRecord(
    user_name="Mohan ji",
    turns=[
        make_turn("s", 1, "Kal Ramesh ko phone kiya"),
        make_turn("s", 2, "Kaisa raha?", role=Role.ASSISTANT),
    ],
    saved=["Memory: Ramesh is his college friend"],
    avoid=["painting"],
)


class FakeMessages:
    def __init__(self, parsed: Analysis | None) -> None:
        self.parsed = parsed
        self.kwargs: dict[str, Any] = {}

    async def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.kwargs = kwargs
        return SimpleNamespace(parsed_output=self.parsed, stop_reason="end_turn")


def test_render_call_lists_saved_items_topics_to_avoid_and_the_transcript() -> None:
    assert render_call(CALL) == (
        "Saved in this call:\n- Memory: Ramesh is his college friend\n\n"
        "Don't mention:\n- painting\n\n"
        "Transcript:\n<transcript>\nMohan ji: Kal Ramesh ko phone kiya\n"
        "Baatein: Kaisa raha?\n</transcript>"
    )
    empty = render_call(CallRecord("Asha", []))
    assert "Saved in this call:\n(nothing)" in empty
    assert "Don't mention:\n(nothing)" in empty


async def test_asks_claude_for_a_structured_summary() -> None:
    messages = FakeMessages(
        Analysis(
            summary="Talked about calling Ramesh.",
            ended_abruptly=False,
            unfinished_topic="",
            notes=["Ask about his garden"],
        )
    )
    analyzer = ClaudeAnalyzer(SimpleNamespace(messages=messages), "claude-haiku-5-5", "low")

    result = await analyzer.analyze(CALL)

    assert result == CallSummary(
        "Talked about calling Ramesh.", unfinished_topic="", notes=("Ask about his garden",)
    )
    sent = messages.kwargs
    assert (sent["model"], sent["output_format"]) == ("claude-haiku-5-5", Analysis)
    assert sent["output_config"] == {"effort": "low"}
    assert "Mohan ji" in sent["system"]
    assert sent["messages"] == [{"role": "user", "content": render_call(CALL)}]
    assert "temperature" not in sent


async def test_a_response_without_a_summary_is_an_error() -> None:
    analyzer = ClaudeAnalyzer(SimpleNamespace(messages=FakeMessages(None)), "m")

    with pytest.raises(ValueError, match="no summary returned"):
        await analyzer.analyze(CALL)
