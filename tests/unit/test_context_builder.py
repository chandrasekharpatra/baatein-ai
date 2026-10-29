from datetime import date

from baatein.domain.models import Certainty, MemoryKind, Person, ScoredFact, Sensitivity
from baatein.memory.context_builder import (
    MAX_SUMMARY_CHARS,
    render_context,
    render_memory,
    render_recall,
    render_search,
)
from baatein.memory.service import MemoryContext
from tests.factories import T0, at, make_fact, make_followup, make_session

TODAY = date(2026, 1, 1)
RAMESH = Person("p1", "u", "Ramesh", T0, relation="college friend")


def context(**overrides: object) -> MemoryContext:
    fields: dict[str, object] = {
        "today": TODAY,
        "summaries": [],
        "followups": [],
        "facts": [],
        "people": [],
    }
    return MemoryContext(**(fields | overrides))  # type: ignore[arg-type]


def test_empty_context_says_nothing_is_saved() -> None:
    text = render_context(context())

    assert text.startswith("Today is Thu 1 Jan 2026.")
    assert "nothing saved about this user" in text


def test_context_lists_memories_people_summaries_and_followups_with_ids() -> None:
    fact = make_fact("u", "Misses Ramesh", about_person_id="p1")
    session = make_session("u", started_at=at(0), summary="x" * (MAX_SUMMARY_CHARS + 50))
    dated = make_followup("u", text="Ask about the interview", due_at=at(60 * 24))
    undated = make_followup("u", text="Ask about the new flat")

    text = render_context(
        context(facts=[fact], people=[RAMESH], summaries=[session], followups=[dated, undated])
    )

    assert f"- [{fact.id}] Misses Ramesh (about Ramesh (college friend))" in text
    assert "People in the user's life: Ramesh (college friend)" in text
    assert f"- Thu 1 Jan 2026: {'x' * MAX_SUMMARY_CHARS}\n" in text + "\n"
    assert "x" * (MAX_SUMMARY_CHARS + 1) not in text
    assert f"- [{dated.id}] Ask about the interview (due Fri 2 Jan 2026)" in text
    assert f"- [{undated.id}] Ask about the new flat" in text
    assert "nothing saved" not in text


def test_memory_line_notes_kind_sensitivity_and_uncertainty() -> None:
    fact = make_fact(
        "u",
        "Gets dizzy sometimes",
        kind=MemoryKind.STORY,
        sensitivity=Sensitivity.HEALTH,
        certainty=Certainty.UNCERTAIN,
    )

    line = render_memory(fact)

    assert line == (
        f"- [{fact.id}] Gets dizzy sometimes (story; health: only if the user brings it up;"
        " the user wasn't sure)"
    )


def test_memory_line_without_person_relation_uses_name_only() -> None:
    fact = make_fact("u", "Neighbour", about_person_id="p2")
    sunita = Person("p2", "u", "Sunita", T0)

    assert render_memory(fact, {"p2": sunita}).endswith("(about Sunita)")


def test_recall_is_empty_without_facts() -> None:
    assert render_recall([]) is None


def test_recall_lists_memories_with_ids() -> None:
    fact = make_fact("u", "Has a dog named Bruno")

    text = render_recall([ScoredFact(fact, 0.5)])

    assert text is not None
    assert f"- [{fact.id}] Has a dog named Bruno" in text


def test_search_reports_nothing_saved() -> None:
    assert render_search([]).startswith("Nothing saved about that.")


def test_search_lists_memories_with_people() -> None:
    fact = make_fact("u", "Wants to call Ramesh again", about_person_id="p1")

    text = render_search([ScoredFact(fact, 0.4)], [RAMESH])

    assert text == f"- [{fact.id}] Wants to call Ramesh again (about Ramesh (college friend))"
