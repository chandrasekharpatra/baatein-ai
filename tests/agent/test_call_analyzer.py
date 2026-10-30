"""The end-of-call analyzer against real Claude, on fixed transcripts (slice 5).

Run with `uv run --env-file .env pytest -m llm --no-cov tests/agent/test_call_analyzer.py`.
"""

from collections.abc import AsyncIterator, Sequence

import pytest
from anthropic import AsyncAnthropic

from baatein.config import FinalizeSettings
from baatein.domain.models import CallSummary, Role
from baatein.memory.analyzer import ClaudeAnalyzer
from baatein.memory.finalizer import clean
from baatein.ports.analyzer import CallRecord
from tests.factories import make_turn

pytestmark = pytest.mark.llm

U, A = Role.USER, Role.ASSISTANT


@pytest.fixture
async def analyzer() -> AsyncIterator[ClaudeAnalyzer]:
    settings = FinalizeSettings()
    async with AsyncAnthropic() as client:
        yield ClaudeAnalyzer(client, settings.model, settings.effort)


async def summarise(
    analyzer: ClaudeAnalyzer,
    lines: Sequence[tuple[Role, str]],
    saved: Sequence[str] = (),
    avoid: Sequence[str] = (),
) -> CallSummary:
    turns = [make_turn("s", n, text, role=role) for n, (role, text) in enumerate(lines, 1)]
    return clean(await analyzer.analyze(CallRecord("Mohan ji", turns, saved, avoid)))


def text_of(result: CallSummary) -> str:
    return " ".join([result.summary or "", *result.notes]).lower()


async def test_an_ordinary_call_gets_one_short_line_and_no_notes(
    analyzer: ClaudeAnalyzer,
) -> None:
    # 10: "Bas theek. Subah chai peeke balcony mein baitha tha."
    result = await summarise(
        analyzer,
        [
            (A, "Namaste Mohan ji, aaj din kaisa raha?"),
            (U, "Bas theek. Subah chai peeke balcony mein baitha tha."),
            (A, "Achha laga sunkar. Balcony mein baithna sukoon deta hai."),
            (U, "Haan. Chalo, ab main aaram karta hoon. Namaste."),
            (A, "Namaste Mohan ji, aaram kijiye."),
        ],
    )

    assert result.summary
    assert len(result.summary) <= 300
    assert result.notes == ()
    assert result.ended_abruptly is False


async def test_details_the_user_did_not_agree_to_keep_stay_out_of_the_summary(
    analyzer: ClaudeAnalyzer,
) -> None:
    # 14: talking openly is not permission; health never becomes a note.
    result = await summarise(
        analyzer,
        [
            (A, "Namaste Mohan ji, kya haal hai?"),
            (U, "Mere bete Ravi ki Pune mein nayi naukri lagi hai, bahut khush hoon."),
            (A, "Arre wah, badhai ho! Ravi ko kaisa lag raha hai?"),
            (U, "Achha lag raha hai. Bas mere ghutne mein dard rehta hai aajkal."),
            (A, "Dhyan rakhiye apna. Doctor ko dikhaya?"),
            (U, "Haan, dawai le raha hoon. Chalo, namaste."),
        ],
    )

    summary = (result.summary or "").lower()
    assert "ravi" not in summary
    assert "pune" not in summary
    assert not any(word in text_of(result) for word in ("knee", "ghutna", "ghutne", "pain"))


async def test_a_call_cut_off_mid_story_names_what_was_being_told(
    analyzer: ClaudeAnalyzer,
) -> None:
    # 31: "Kal main apne bachpan ka ek kissa bata raha tha..." [call drops]
    result = await summarise(
        analyzer,
        [
            (A, "Namaste Mohan ji!"),
            (U, "Namaste. Aaj main tumhe apne bachpan ka ek kissa sunata hoon."),
            (A, "Zaroor, sunaiye."),
            (
                U,
                "Hamare gaon mein ek bada mela lagta tha, aur ek baar main wahan kho gaya tha, aur",
            ),
        ],
    )

    assert result.ended_abruptly is True
    assert result.unfinished_topic
    topic = result.unfinished_topic.lower()
    assert any(word in topic for word in ("childhood", "village", "fair", "mela", "lost"))


async def test_a_saved_item_may_be_named_but_a_boundary_never(
    analyzer: ClaudeAnalyzer,
) -> None:
    result = await summarise(
        analyzer,
        [
            (U, "Ramesh mera college ka dost hai, Sunday ko usse phone karunga. Yaad rakhna."),
            (A, "Zaroor, yaad rakh liya. Ramesh ji ko Sunday ko phone karna hai."),
            (U, "Aur haan, aaj maine thodi painting bhi ki."),
            (A, "Achha!"),
            (U, "Chalo, namaste."),
        ],
        saved=["Memory: Ramesh is his college friend; he plans to call him on Sunday"],
        avoid=["painting"],
    )

    assert "ramesh" in (result.summary or "").lower()
    assert "paint" not in text_of(result)
    assert result.notes == ()  # already saved, so nothing to offer


async def test_hanging_up_after_a_complete_answer_is_a_normal_end(
    analyzer: ClaudeAnalyzer,
) -> None:
    result = await summarise(
        analyzer,
        [
            (A, "Did you finish the pending deployment for your project?"),
            (U, "Yes, yes, I was able to do that."),
        ],
    )

    assert result.ended_abruptly is False
