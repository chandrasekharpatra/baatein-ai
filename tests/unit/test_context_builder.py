from datetime import date

from baatein.domain.models import Role, ScoredFact
from baatein.memory.context_builder import (
    MAX_SUMMARY_CHARS,
    render_context,
    render_recall,
    render_search,
)
from baatein.memory.service import MemoryContext, SearchResult
from tests.factories import at, make_fact, make_followup, make_session, make_turn

TODAY = date(2026, 1, 1)


def test_empty_context_says_nothing_is_remembered() -> None:
    text = render_context(MemoryContext(TODAY, [], [], []))

    assert text.startswith("Today is Thu 1 Jan 2026.")
    assert "don't remember anything" in text


def test_context_lists_facts_summaries_and_followups_with_ids() -> None:
    long_summary = "x" * (MAX_SUMMARY_CHARS + 50)
    session = make_session("u", started_at=at(0), summary=long_summary)
    dated = make_followup("u", text="Ask about the interview", due_at=at(60 * 24))
    undated = make_followup("u", text="Ask about the new flat")
    context = MemoryContext(TODAY, [session], [dated, undated], [make_fact("u", "Works at TCS")])

    text = render_context(context)

    assert "- Works at TCS" in text
    assert f"- Thu 1 Jan 2026: {'x' * MAX_SUMMARY_CHARS}\n" in text + "\n"
    assert "x" * (MAX_SUMMARY_CHARS + 1) not in text
    assert f"- [{dated.id}] Ask about the interview (due Fri 2 Jan 2026)" in text
    assert f"- [{undated.id}] Ask about the new flat" in text
    assert "resolve_followup" in text
    assert "don't remember anything" not in text


def test_recall_is_empty_without_facts() -> None:
    assert render_recall([]) is None


def test_recall_lists_facts() -> None:
    text = render_recall([ScoredFact(make_fact("u", "Has a dog named Bruno"), 0.5)])

    assert text is not None
    assert "- Has a dog named Bruno" in text


def test_search_reports_nothing_found() -> None:
    assert render_search(SearchResult([], [])) == "Nothing found in memory."


def test_search_lists_facts_and_turns_by_speaker() -> None:
    result = SearchResult(
        [ScoredFact(make_fact("u", "Sister is Priya"), 0.4)],
        [
            make_turn("s", 1, "Priya ki shaadi hai", created_at=at(0)),
            make_turn("s", 2, "Wah!", role=Role.ASSISTANT, created_at=at(1)),
        ],
    )

    text = render_search(result)

    assert "- Sister is Priya" in text
    assert "- Thu 1 Jan 2026, User: Priya ki shaadi hai" in text
    assert "- Thu 1 Jan 2026, You: Wah!" in text


def test_search_with_only_turns_skips_facts_heading() -> None:
    text = render_search(SearchResult([], [make_turn("s", 1, "Goa trip", created_at=at(0))]))

    assert "Facts:" not in text
    assert text.startswith("From earlier calls:")
