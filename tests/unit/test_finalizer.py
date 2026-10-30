import asyncio
from dataclasses import replace
from datetime import date, timedelta

import pytest

from baatein.config import FinalizeSettings
from baatein.domain.errors import NotFoundError
from baatein.domain.models import (
    CallSummary,
    FinalizeStatus,
    Preferences,
    Role,
    Sensitivity,
    Session,
    ThreadEvent,
    ThreadStatus,
)
from baatein.memory import finalizer as finalizer_module
from baatein.memory.finalizer import PAUSED_SUMMARY, Finalizer, clean
from baatein.ports.analyzer import CallRecord
from tests.factories import (
    at,
    make_boundary,
    make_fact,
    make_reminder,
    make_session,
    make_thread,
    make_turn,
)
from tests.fakes.analyzer import FakeAnalyzer
from tests.fakes.world import World, build_world

SETTINGS = FinalizeSettings(retry_base_seconds=60, retry_max_seconds=150, max_attempts=3)


async def ended_call(world: World, *lines: tuple[Role, str], **fields: object) -> Session:
    session = make_session(world.user.id, **fields)
    await world.deps.sessions.add(session)
    for seq, (role, text) in enumerate(lines, start=1):
        await world.deps.turns.append(make_turn(session.id, seq, text, role=role))
    await world.deps.sessions.end(session.id, world.clock.now(), "participant_disconnected")
    stored = await world.deps.sessions.get(session.id)
    assert stored is not None
    return stored


async def stored(world: World, session_id: str) -> Session:
    session = await world.deps.sessions.get(session_id)
    assert session is not None
    return session


CHAT = ((Role.USER, "Bas theek. Subah chai peeke balcony mein baitha tha."),)


async def test_summarises_a_finished_call_with_only_what_the_user_agreed_to_keep() -> None:
    world = build_world()
    world.state.users[world.user.id] = replace(
        world.user, display_name="Mohan", preferences=Preferences(address_as="Mohan ji")
    )
    call = await ended_call(world, *CHAT, (Role.ASSISTANT, "Achha laga sunkar."))
    other = make_session(world.user.id)
    await world.deps.sessions.add(other)
    for fact in (
        make_fact(world.user.id, "Ramesh is his college friend", source_session_id=call.id),
        make_fact(
            world.user.id,
            "Has knee pain",
            source_session_id=call.id,
            sensitivity=Sensitivity.HEALTH,
        ),
        make_fact(world.user.id, "Likes chai", source_session_id=other.id),
    ):
        await world.store.add(fact)
    await world.deps.threads.add(
        make_thread(world.user.id, "Call Ramesh", source_session_id=call.id, created_at=at(1))
    )
    old = make_thread(world.user.id, "Visit to Puri", source_session_id=other.id, created_at=at(2))
    await world.deps.threads.add(old)
    await world.deps.threads.add_event(
        ThreadEvent("e1", old.id, "Went", world.clock.now(), ThreadStatus.DONE, call.id)
    )
    await world.deps.threads.add(
        make_thread(
            world.user.id,
            "Worry about Rohan",
            source_session_id=call.id,
            sensitivity=Sensitivity.PERSONAL,
        )
    )
    for title, on in (("Ravi's birthday", date(2026, 11, 18)), ("June visit", None)):
        await world.deps.reminders.add(
            make_reminder(world.user.id, title, on=on, source_session_id=call.id)
        )
    await world.deps.boundaries.add(make_boundary(world.user.id, "painting"))
    analyzer = FakeAnalyzer()

    done = await Finalizer(world.deps, analyzer, SETTINGS).run_due()

    assert done == 1
    [record] = analyzer.calls
    assert record.user_name == "Mohan ji"
    assert [t.text for t in record.turns] == [CHAT[0][1], "Achha laga sunkar."]
    assert record.saved == [
        "Memory: Ramesh is his college friend",
        "Storyline: Call Ramesh (open)",
        "Storyline: Visit to Puri (open)",
        "Reminder: Ravi's birthday on 18 Nov 2026",
        "Reminder: June visit (no date yet)",
    ]
    assert record.avoid == ["painting"]
    finished = await stored(world, call.id)
    assert finished.finalize_status is FinalizeStatus.DONE
    assert finished.summary == "A short, light chat about his day."


@pytest.mark.parametrize(("display_name", "expected"), [("Asha", "Asha"), (None, "the user")])
async def test_names_the_user_by_display_name_or_generically(
    display_name: str | None, expected: str
) -> None:
    world = build_world()
    world.state.users[world.user.id] = replace(world.user, display_name=display_name)
    await ended_call(world, *CHAT)
    analyzer = FakeAnalyzer()

    await Finalizer(world.deps, analyzer, SETTINGS).run_due()

    assert analyzer.calls[0].user_name == expected


def test_clean_redacts_trims_and_drops_what_does_not_apply() -> None:
    result = clean(
        CallSummary(
            "  Shared his OTP 482193 by mistake. " + "x" * 400,
            ended_abruptly=False,
            unfinished_topic="the mela",
            notes=("Ask about Puri", "   ", "Card 4111 1111 1111 1111", "Third"),
        )
    )

    assert result.summary is not None
    assert result.summary.startswith("Shared his OTP [redacted] by mistake.")
    assert len(result.summary) == 300
    assert (result.ended_abruptly, result.unfinished_topic) == (False, None)
    assert result.notes == ("Ask about Puri", "Card [redacted]")
    assert clean(CallSummary("  ", ended_abruptly=True, unfinished_topic=" mela ")) == (
        CallSummary(None, ended_abruptly=True, unfinished_topic="mela")
    )
    assert clean(CallSummary(None, ended_abruptly=True)).unfinished_topic is None
    baatein_last = clean(
        CallSummary("x", ended_abruptly=True, unfinished_topic="mela"), user_spoke_last=False
    )
    assert (baatein_last.ended_abruptly, baatein_last.unfinished_topic) == (False, None)


async def test_a_call_where_saving_was_paused_is_not_sent_to_claude() -> None:
    world = build_world()
    session = make_session(world.user.id)
    await world.deps.sessions.add(session)
    await world.deps.turns.append(make_turn(session.id, 1, "Mera PIN 4821 hai"))
    await world.deps.sessions.end(session.id, world.clock.now(), None, saving_paused=True)
    analyzer = FakeAnalyzer()

    await Finalizer(world.deps, analyzer, SETTINGS).run_due()

    assert analyzer.calls == []
    assert (await stored(world, session.id)).summary == PAUSED_SUMMARY


async def test_a_call_where_the_user_never_spoke_gets_no_summary() -> None:
    world = build_world()
    call = await ended_call(world, (Role.ASSISTANT, "Namaste!"))
    analyzer = FakeAnalyzer()

    await Finalizer(world.deps, analyzer, SETTINGS).run_due()

    assert analyzer.calls == []
    finished = await stored(world, call.id)
    assert (finished.finalize_status, finished.summary) == (FinalizeStatus.DONE, None)


async def test_a_call_with_only_fillers_gets_no_summary() -> None:
    # "बढ़िया" and "ठीक" got a summary that then opened the next call instead of a real one.
    world = build_world()
    call = await ended_call(
        world, (Role.USER, "बढ़िया।"), (Role.ASSISTANT, "Aaj kya kiya?"), (Role.USER, "ठीक।")
    )
    analyzer = FakeAnalyzer()

    await Finalizer(world.deps, analyzer, SETTINGS).run_due()

    assert analyzer.calls == []
    assert (await stored(world, call.id)).summary is None


async def test_a_short_call_that_saved_something_is_summarised() -> None:
    world = build_world()
    call = await ended_call(world, (Role.USER, "Haan, yaad rakhna"))
    await world.store.add(make_fact(world.user.id, "Loves masala chai", source_session_id=call.id))
    analyzer = FakeAnalyzer()

    await Finalizer(world.deps, analyzer, SETTINGS).run_due()

    assert len(analyzer.calls) == 1


async def test_the_summary_replaces_the_guess_made_when_the_call_ended() -> None:
    world = build_world()
    session = make_session(world.user.id)
    await world.deps.sessions.add(session)
    await world.deps.turns.append(make_turn(session.id, 1, "Kal main gaon gaya tha aur phir"))
    await world.deps.sessions.end(
        session.id, world.clock.now(), None, ended_abruptly=True, unfinished_topic="Kal main"
    )
    analyzer = FakeAnalyzer(
        CallSummary(
            "Started telling about a trip to his village.",
            ended_abruptly=True,
            unfinished_topic="a trip to his village",
            notes=("Mentioned his village fair; ask if he'd like it remembered",),
        )
    )

    await Finalizer(world.deps, analyzer, SETTINGS).run_due()

    finished = await stored(world, session.id)
    assert finished.unfinished_topic == "a trip to his village"
    assert finished.notes == ("Mentioned his village fair; ask if he'd like it remembered",)


async def test_failures_are_retried_with_growing_waits_then_given_up() -> None:
    world = build_world()
    call = await ended_call(world, *CHAT)
    analyzer = FakeAnalyzer(error=RuntimeError("Claude is down"))
    finalizer = Finalizer(world.deps, analyzer, SETTINGS)
    start = world.clock.now()

    assert await finalizer.run_due() == 0
    first = await stored(world, call.id)
    assert await finalizer.run_due() == 0  # not due yet
    world.clock.advance(timedelta(seconds=60))
    await finalizer.run_due()
    second = await stored(world, call.id)
    world.clock.advance(timedelta(seconds=150))
    await finalizer.run_due()
    third = await stored(world, call.id)
    world.clock.advance(timedelta(days=1))
    await finalizer.run_due()

    assert len(analyzer.calls) == 3  # gave up after max_attempts
    assert (first.finalize_status, first.finalize_attempts) == (FinalizeStatus.FAILED, 1)
    assert first.finalize_retry_at == start + timedelta(seconds=60)
    assert second.finalize_retry_at == start + timedelta(seconds=60 + 120)
    assert third.finalize_retry_at == start + timedelta(seconds=210 + 150)  # capped
    assert third.finalize_attempts == 3


async def test_a_slow_analyzer_counts_as_a_failure() -> None:
    world = build_world()
    call = await ended_call(world, *CHAT)

    class SlowAnalyzer(FakeAnalyzer):
        async def analyze(self, call: CallRecord) -> CallSummary:
            await asyncio.sleep(1)
            return self.result

    settings = SETTINGS.model_copy(update={"timeout_seconds": 0.01})
    await Finalizer(world.deps, SlowAnalyzer(), settings).run_due()

    assert (await stored(world, call.id)).finalize_status is FinalizeStatus.FAILED


async def test_a_call_whose_user_is_gone_fails() -> None:
    world = build_world()
    call = await ended_call(world, *CHAT)
    del world.state.users[world.user.id]

    await Finalizer(world.deps, FakeAnalyzer(), SETTINGS).run_due()

    assert (await stored(world, call.id)).finalize_status is FinalizeStatus.FAILED


async def test_finalize_by_id_runs_one_call_again() -> None:
    world = build_world()
    call = await ended_call(world, *CHAT)
    finalizer = Finalizer(world.deps, FakeAnalyzer(), SETTINGS)
    await finalizer.run_due()

    again = FakeAnalyzer(CallSummary("Talked about chai."))
    assert await Finalizer(world.deps, again, SETTINGS).finalize_by_id(call.id) is True
    assert (await stored(world, call.id)).summary == "Talked about chai."
    with pytest.raises(NotFoundError):
        await finalizer.finalize_by_id("missing")


async def test_run_forever_keeps_going_after_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    world = build_world()
    finalizer = Finalizer(world.deps, FakeAnalyzer(), SETTINGS)
    runs: list[str] = []

    async def run_due() -> int:
        runs.append("run")
        if len(runs) == 1:
            raise RuntimeError("database locked")
        return 0

    async def sleep(seconds: float) -> None:
        assert seconds == SETTINGS.interval_seconds
        if len(runs) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(finalizer, "run_due", run_due)
    monkeypatch.setattr(finalizer_module.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await finalizer.run_forever()
    assert runs == ["run", "run"]
