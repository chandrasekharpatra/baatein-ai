import asyncio
import logging
from dataclasses import replace
from datetime import date, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from baatein.config import MemorySettings
from baatein.domain.models import (
    BoundaryScope,
    Certainty,
    Fact,
    FinalizeStatus,
    MemoryKind,
    OccurrenceStatus,
    Pace,
    Preferences,
    ReminderStatus,
    Repeat,
    Role,
    ScoredFact,
    Sensitivity,
    ThreadKind,
    ThreadStatus,
    Verbosity,
)
from baatein.memory.service import (
    FORGOTTEN_TURN,
    AlreadySaved,
    CallMemory,
    ForgetOutcome,
    MemoryService,
    split_relation,
    zone,
)
from tests.factories import (
    at,
    make_boundary,
    make_fact,
    make_reminder,
    make_session,
    make_thread,
    make_turn,
    make_user,
)
from tests.fakes.world import World, build_world


@pytest.fixture
def world() -> World:
    return build_world()


async def test_open_call_creates_active_session(world: World) -> None:
    memory = await world.service.open_call(world.user, room_name="room-1", language="hinglish")

    stored = world.state.sessions[memory.session.id]
    assert stored.user_id == world.user.id
    assert stored.room_name == "room-1"
    assert stored.started_at == world.clock.now()
    assert stored.finalize_status is FinalizeStatus.ACTIVE
    assert memory.user_id == world.user.id


async def test_warm_up_embeds_once(world: World) -> None:
    await world.service.warm_up()

    assert world.embedder.calls == [(["warm up"], "query")]


async def test_warm_up_swallows_errors(world: World, caplog: pytest.LogCaptureFixture) -> None:
    async def broken(*_: object) -> list[list[float]]:
        raise RuntimeError("model download failed")

    world.embedder.embed = broken  # type: ignore[method-assign]

    await world.service.warm_up()

    assert "embedder warm-up failed" in caplog.text


async def test_record_writes_turns_in_order_with_language(world: World) -> None:
    memory = await world.service.open_call(world.user, language="hinglish")

    memory.record(Role.ASSISTANT, "Namaste!")
    memory.record(Role.USER, "Kal mera interview hai", interrupted=True)
    await memory.flush()

    turns = world.state.turns
    assert [(t.seq, t.role, t.text) for t in turns] == [
        (1, Role.ASSISTANT, "Namaste!"),
        (2, Role.USER, "Kal mera interview hai"),
    ]
    assert all(t.language == "hinglish" for t in turns)
    assert [t.interrupted for t in turns] == [False, True]


async def test_flush_without_turns_is_a_noop(world: World) -> None:
    memory = await world.service.open_call(world.user)

    await memory.flush()

    assert world.state.turns == []


async def test_failed_turn_write_is_logged_and_later_turns_still_saved(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    memory = await world.service.open_call(world.user)
    world.state.turns.append(make_turn(memory.session.id, 1, "already there"))

    memory.record(Role.USER, "clashes with seq 1")
    memory.record(Role.USER, "second")
    await memory.flush()

    assert "failed to save turn 1" in caplog.text
    assert [t.text for t in world.state.turns] == ["already there", "second"]


async def test_close_flushes_turns_then_ends_session(world: World) -> None:
    memory = await world.service.open_call(world.user)
    memory.record(Role.USER, "bye")
    world.clock.advance(timedelta(minutes=5))

    await memory.close("user_left")

    session = world.state.sessions[memory.session.id]
    assert [t.text for t in world.state.turns] == ["bye"]
    assert session.ended_at == world.clock.now()
    assert session.end_reason == "user_left"
    assert session.finalize_status is FinalizeStatus.PENDING
    assert (session.ended_abruptly, session.unfinished_topic) == (False, None)


async def test_close_after_the_user_was_cut_off_mid_story_keeps_the_topic(world: World) -> None:
    memory = await world.service.open_call(world.user)
    memory.record(Role.ASSISTANT, "Phir kya hua?")
    memory.record(Role.USER, "Phir hum gaon ke mele mein gaye aur wahan " + "bahut " * 60)

    await memory.close("participant_disconnected")

    session = world.state.sessions[memory.session.id]
    assert session.ended_abruptly
    assert session.unfinished_topic is not None
    assert session.unfinished_topic.startswith("Phir hum gaon ke mele mein gaye")
    assert len(session.unfinished_topic) == 200


async def test_close_after_a_reply_or_a_short_goodbye_is_not_abrupt(world: World) -> None:
    replied = await world.service.open_call(world.user)
    replied.record(Role.USER, "Aaj bahut achha din tha mera")
    replied.record(Role.ASSISTANT, "Kitni achhi baat hai!")
    goodbye = await world.service.open_call(world.user)
    goodbye.record(Role.USER, "Achha, bye")

    for memory in (replied, goodbye):
        await memory.close()
        assert not world.state.sessions[memory.session.id].ended_abruptly


async def test_forgetting_a_memory_from_this_call_leaves_no_unfinished_topic(
    world: World,
) -> None:
    memory = await world.service.open_call(world.user)
    memory.record(Role.USER, "Mere cousin Mahesh ne Pune mein flat liya hai")
    fact = await memory.remember("Cousin Mahesh bought a flat in Pune")
    assert fact is not None
    await memory.forget(fact.id, user_turn=1)
    await memory.forget(fact.id, user_turn=2)

    await memory.close()

    assert not world.state.sessions[memory.session.id].ended_abruptly


async def test_close_is_idempotent_and_drops_late_turns(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    memory = await world.service.open_call(world.user)
    await memory.close()
    ended_at = world.state.sessions[memory.session.id].ended_at
    world.clock.advance(timedelta(minutes=1))

    await memory.close("again")
    memory.record(Role.USER, "too late")
    await memory.flush()

    assert world.state.sessions[memory.session.id].ended_at == ended_at
    assert world.state.turns == []
    assert "dropping turn" in caplog.text


async def test_context_for_a_first_call(world: World) -> None:
    memory = await world.service.open_call(world.user)

    context = await memory.context()

    assert context.last_call is None
    assert context.now == world.clock.now()
    assert context.now.tzinfo == ZoneInfo("Asia/Kolkata")
    assert (context.facts, context.threads, context.check_in) == ([], [], None)


async def test_context_loads_last_call_memories_boundaries_and_declined(world: World) -> None:
    user_id = world.user.id
    older = make_session(user_id, started_at=at(-3000), summary="Talked about exams.")
    last = make_session(user_id, started_at=at(-2000))
    other_user = make_user()
    world.state.users[other_user.id] = other_user
    for session in (older, last, make_session(other_user.id)):
        world.state.sessions[session.id] = session
    await world.store.add(make_fact(user_id, "Works at Infosys", importance=5))
    boundary = make_boundary(user_id, "painting")
    world.state.boundaries[boundary.id] = boundary
    memory = await world.service.open_call(world.user)
    await memory.note_declined("go for a walk")

    context = await memory.context()

    assert context.last_call == last
    assert context.earlier_call == older  # the last call has no summary
    assert [f.text for f in context.facts] == ["Works at Infosys"]
    assert context.boundaries == [boundary]
    assert [d.text for d in context.declined] == ["go for a walk"]


async def test_context_needs_no_earlier_call_when_the_last_has_a_summary(world: World) -> None:
    for minutes, summary in ((-3000, "Talked about exams."), (-2000, "Talked about chai.")):
        session = make_session(world.user.id, started_at=at(minutes), summary=summary)
        world.state.sessions[session.id] = session
    memory = await world.service.open_call(world.user)

    context = await memory.context()

    assert context.last_call is not None
    assert context.last_call.summary == "Talked about chai."
    assert context.earlier_call is None


async def test_context_picks_one_check_in_and_marks_it_offered(world: World) -> None:
    thread = make_thread(world.user.id)
    world.state.threads[thread.id] = thread
    memory = await world.service.open_call(world.user)
    await memory.update_thread(thread.id, event="Plans to call him")

    context = await memory.context()

    assert context.check_in is not None
    assert context.check_in.id == thread.id
    assert (context.check_in.times_offered, context.check_in.last_offered_at) == (
        1,
        world.clock.now(),
    )
    assert world.state.threads[thread.id] == context.check_in
    assert [e.text for e in context.check_in_events] == ["Plans to call him"]
    again = await (await world.service.open_call(world.user)).context()
    assert again.check_in is None


async def test_context_offers_the_cut_off_story_instead_of_a_check_in(world: World) -> None:
    last = make_session(
        world.user.id, started_at=at(-60), ended_abruptly=True, unfinished_topic="Gaon ka mela"
    )
    world.state.sessions[last.id] = last
    thread = make_thread(world.user.id)
    world.state.threads[thread.id] = thread

    context = await (await world.service.open_call(world.user)).context()

    assert context.last_call == last
    assert context.check_in is None
    assert world.state.threads[thread.id].times_offered == 0


async def test_context_respects_limits() -> None:
    world = build_world(MemorySettings(context_facts=1, context_threads=1, context_declined=1))
    for minutes in (-30, -20, -10):
        await world.store.add(make_fact(world.user.id, f"fact {minutes}"))
        thread = make_thread(world.user.id, created_at=at(minutes))
        world.state.threads[thread.id] = thread
    memory = await world.service.open_call(world.user)
    for text in ("walk", "yoga"):
        await memory.note_declined(text)

    context = await memory.context()

    assert (len(context.facts), len(context.threads), len(context.declined)) == (1, 1, 1)


def test_zone_falls_back_to_india_for_unknown_names(caplog: pytest.LogCaptureFixture) -> None:
    assert zone("Europe/London") == ZoneInfo("Europe/London")
    assert zone("Mars/Base") == ZoneInfo("Asia/Kolkata")
    assert zone("") == ZoneInfo("Asia/Kolkata")
    assert "unknown timezone" in caplog.text


async def test_open_call_uses_the_users_timezone(world: World) -> None:
    user = make_user(timezone="Europe/London")
    world.state.users[user.id] = user

    memory = await world.service.open_call(user)

    assert memory.timezone == ZoneInfo("Europe/London")


async def test_recall_returns_relevant_facts(world: World) -> None:
    await world.store.add(make_fact(world.user.id, "Has a job interview at Google"))
    await world.store.add(make_fact(world.user.id, "Loves masala chai"))
    memory = await world.service.open_call(world.user)

    recalled = await memory.recall("google interview")

    assert recalled[0].fact.text == "Has a job interview at Google"


async def test_a_slow_recall_stops_waiting_but_keeps_searching(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = build_world(MemorySettings(recall_timeout_seconds=0.01))
    await world.store.add(make_fact(world.user.id, "Has a job interview at Google"))
    search = world.store.search

    async def slow(*args: Any) -> list[ScoredFact]:
        await asyncio.sleep(0.05)
        return await search(*args)

    world.store.search = slow  # type: ignore[method-assign]
    memory = await world.service.open_call(world.user)
    task = memory.start_recall("google interview")

    with caplog.at_level(logging.INFO):
        assert await memory.recall(task) is None
        assert "still searching" in caplog.text
        [found] = await task  # not cancelled: the caller can use it later
    assert found.fact.text == "Has a job interview at Google"
    assert "the search took" in caplog.text


async def test_recall_of_a_cancelled_search_finds_nothing(world: World) -> None:
    memory = await world.service.open_call(world.user)
    task = memory.start_recall("anything")
    task.cancel()

    assert await memory.recall(task) == []


async def test_recall_swallows_errors(world: World, caplog: pytest.LogCaptureFixture) -> None:
    async def broken(*_: object) -> list[ScoredFact]:
        raise RuntimeError("db locked")

    world.store.search = broken  # type: ignore[method-assign]
    memory = await world.service.open_call(world.user)

    with caplog.at_level(logging.ERROR):
        assert await memory.recall("anything") == []
    assert "memory recall failed" in caplog.text


async def test_search_returns_saved_memories_never_transcripts(world: World) -> None:
    await world.store.add(make_fact(world.user.id, "Sister Priya is getting married"))
    earlier = make_session(world.user.id)
    world.state.sessions[earlier.id] = earlier
    world.state.turns.append(make_turn(earlier.id, 1, "Priya ki shaadi December mein hai"))
    memory = await world.service.open_call(world.user)

    result = await memory.search("Priya")

    assert [s.fact.text for s in result] == ["Sister Priya is getting married"]


async def test_record_redacts_codes_before_saving(world: World) -> None:
    memory = await world.service.open_call(world.user)

    memory.record(Role.USER, "Usne OTP maanga, maine bola OTP hai 4821")
    await memory.flush()

    assert world.state.turns[0].text == "Usne OTP maanga, maine bola OTP hai [redacted]"


async def test_remember_saves_consented_memory_about_a_new_person(world: World) -> None:
    memory = await world.service.open_call(world.user)

    fact = await memory.remember(
        "Misses college friend Ramesh",
        kind=MemoryKind.STORY,
        quote="Hum har shaam saath chai peete the",
        about="Ramesh",
        relation="college friend",
        sensitivity=Sensitivity.PERSONAL,
        certainty=Certainty.UNCERTAIN,
    )

    assert fact is not None
    stored = await world.store.get(fact.id)
    assert stored == fact
    assert fact.source_session_id == memory.session.id
    [ramesh] = world.state.people.values()
    assert (ramesh.name, ramesh.relation, fact.about_person_id) == (
        "Ramesh",
        "college friend",
        ramesh.id,
    )
    assert await memory.people() == [ramesh]


async def test_remember_reuses_person_and_updates_relation(world: World) -> None:
    memory = await world.service.open_call(world.user)
    first = await memory.remember("Ravi called", about="Ravi")
    second = await memory.remember("Ravi's birthday is 18 Nov", about="ravi", relation="son")
    third = await memory.remember("Ravi likes cricket", about="Ravi", relation="son")

    [ravi] = world.state.people.values()
    assert ravi.relation == "son"
    assert {f.about_person_id for f in (first, second, third) if f} == {ravi.id}


async def test_remember_redacts_text_and_quote(world: World) -> None:
    memory = await world.service.open_call(world.user)

    fact = await memory.remember("Got a scam call asking for OTP 5521", quote="PIN 1234 maanga")

    assert fact is not None
    assert fact.text == "Got a scam call asking for OTP [redacted]"
    assert fact.quote == "PIN [redacted] maanga"


async def test_remember_is_refused_while_saving_is_paused(world: World) -> None:
    memory = await world.service.open_call(world.user)

    memory.pause_saving()

    assert memory.saving_paused
    assert await memory.remember("Likes gardening") is None
    assert await world.store.active(world.user.id, limit=5) == []


async def test_correct_replaces_memory_keeping_its_details(world: World) -> None:
    memory = await world.service.open_call(world.user)
    old = await memory.remember(
        "Suman is my sister", about="Suman", sensitivity=Sensitivity.PERSONAL, quote="Suman"
    )
    assert old is not None

    new = await memory.correct(old.id, "Sunita is my neighbour")

    assert new is not None
    assert (new.text, new.about_person_id, new.sensitivity, new.quote) == (
        "Sunita is my neighbour",
        old.about_person_id,
        Sensitivity.PERSONAL,
        "Suman",
    )
    assert [f.id for f in await world.store.active(world.user.id, limit=5)] == [new.id]
    newer = await memory.correct(new.id, "Sunita moved away", quote="dusre shehar gayi")
    assert newer is not None
    assert newer.quote == "dusre shehar gayi"


async def test_correct_rejects_unknown_foreign_or_retired_memory(world: World) -> None:
    other = make_user()
    world.state.users[other.id] = other
    foreign = make_fact(other.id)
    await world.store.add(foreign)
    memory = await world.service.open_call(world.user)
    old = await memory.remember("Old")
    assert old is not None
    await memory.correct(old.id, "New")

    assert await memory.correct("missing", "x") is None
    assert await memory.correct(foreign.id, "x") is None
    assert await memory.correct(old.id, "x") is None


async def confirmed_forget(memory: CallMemory, fact_id: str) -> ForgetOutcome:
    assert await memory.forget(fact_id, user_turn=1) is ForgetOutcome.NEEDS_CONFIRMATION
    return await memory.forget(fact_id, user_turn=2)


async def test_forget_needs_confirmation_on_the_next_user_turn(world: World) -> None:
    memory = await world.service.open_call(world.user)
    fact = await memory.remember("Private family matter")
    assert fact is not None

    assert await memory.forget(fact.id, user_turn=3) is ForgetOutcome.NEEDS_CONFIRMATION
    assert await memory.forget(fact.id, user_turn=3) is ForgetOutcome.NEEDS_CONFIRMATION
    assert not (await world.store.get(fact.id)).is_deleted  # type: ignore[union-attr]

    assert await memory.forget(fact.id, user_turn=4) is ForgetOutcome.DELETED
    assert (await world.store.get(fact.id)).is_deleted  # type: ignore[union-attr]


async def test_unconfirmed_forget_request_expires(world: World) -> None:
    memory = await world.service.open_call(world.user)
    fact = await memory.remember("Private family matter")
    assert fact is not None
    await memory.forget(fact.id, user_turn=1)

    assert await memory.forget(fact.id, user_turn=3) is ForgetOutcome.NEEDS_CONFIRMATION
    assert not (await world.store.get(fact.id)).is_deleted  # type: ignore[union-attr]


async def test_forget_wipes_memory_and_redacts_its_calls(world: World) -> None:
    earlier = await world.service.open_call(world.user)
    earlier.record(Role.USER, "Family wali baat")
    fact = await earlier.remember("Private family matter")
    assert fact is not None
    await earlier.close()
    world.state.sessions[earlier.session.id] = replace(
        world.state.sessions[earlier.session.id], summary="Talked about family"
    )
    memory = await world.service.open_call(world.user)
    memory.record(Role.USER, "Woh family wali baat bhool jao")

    assert await confirmed_forget(memory, fact.id) is ForgetOutcome.DELETED

    tombstone = await world.store.get(fact.id)
    assert tombstone is not None
    assert tombstone.is_deleted
    assert tombstone.text == ""
    assert [t.text for t in world.state.turns] == [FORGOTTEN_TURN, "Woh family wali baat bhool jao"]
    assert world.state.sessions[earlier.session.id].summary is None


async def test_forget_removes_a_person_only_when_nothing_else_is_about_them(world: World) -> None:
    memory = await world.service.open_call(world.user)
    first = await memory.remember("Misses Ramesh", about="Ramesh")
    second = await memory.remember("Ramesh lives in Pune", about="Ramesh")
    assert first is not None
    assert second is not None

    await confirmed_forget(memory, first.id)
    assert [p.name for p in await memory.people()] == ["Ramesh"]
    await confirmed_forget(memory, second.id)
    assert await memory.people() == []


async def test_forget_rejects_unknown_foreign_or_already_deleted(world: World) -> None:
    other = make_user()
    world.state.users[other.id] = other
    foreign = make_fact(other.id)
    await world.store.add(foreign)
    memory = await world.service.open_call(world.user)
    fact = await memory.remember("Secret")
    assert fact is not None
    await confirmed_forget(memory, fact.id)

    assert await memory.forget("missing", user_turn=3) is ForgetOutcome.NOT_FOUND
    assert await memory.forget(foreign.id, user_turn=3) is ForgetOutcome.NOT_FOUND
    assert await memory.forget(fact.id, user_turn=3) is ForgetOutcome.NOT_FOUND
    assert (await world.store.get(foreign.id)).text == foreign.text  # type: ignore[union-attr]


async def test_rename_person_fixes_the_name(world: World) -> None:
    memory = await world.service.open_call(world.user)
    suman = await memory.person("Suman", "neighbour")

    renamed = await memory.rename_person("suman", "Sunita")

    assert renamed is not None
    assert world.state.people[suman.id].name == "Sunita"
    assert world.state.people[suman.id].relation == "neighbour"
    assert await memory.rename_person("Nobody", "X") is None


async def test_track_thread_starts_an_open_storyline_about_a_person(world: World) -> None:
    memory = await world.service.open_call(world.user)

    thread = await memory.track_thread(
        "Reconnect with Ramesh OTP 482913",
        kind=ThreadKind.RELATIONSHIP,
        next_step="Call him",
        check_in_after=at(60),
        about="Ramesh",
        relation="college friend",
    )

    assert thread is not None
    stored = world.state.threads[thread.id]
    assert stored.title == "Reconnect with Ramesh OTP [redacted]"
    assert (stored.status, stored.next_step, stored.check_in_after) == (
        ThreadStatus.OPEN,
        "Call him",
        at(60),
    )
    assert stored.source_session_id == memory.session.id
    assert world.state.people[stored.about_person_id or ""].name == "Ramesh"


async def test_update_thread_moves_the_story_and_logs_events(world: World) -> None:
    """Scenario #1 over three calls: wants to call, will call, called."""
    thread = make_thread(world.user.id, times_offered=2)
    world.state.threads[thread.id] = thread
    memory = await world.service.open_call(world.user)

    await memory.update_thread(
        thread.id, event="Plans to call him", status=ThreadStatus.IN_PROGRESS, next_step="Call"
    )
    world.clock.advance(timedelta(days=2))
    updated = await memory.update_thread(thread.id, event="Called Ramesh", status=ThreadStatus.DONE)
    await memory.update_thread(thread.id, status=ThreadStatus.DONE)
    await memory.update_thread(thread.id, check_in_after=at(9))

    assert updated is not None
    stored = world.state.threads[thread.id]
    assert (stored.status, stored.next_step, stored.times_offered) == (
        ThreadStatus.DONE,
        "Call",
        0,
    )
    assert stored.check_in_after == at(9)
    assert [(e.text, e.status) for e in world.state.thread_events] == [
        ("Plans to call him", ThreadStatus.IN_PROGRESS),
        ("Called Ramesh", ThreadStatus.DONE),
        ("Marked done", ThreadStatus.DONE),
    ]
    assert world.state.thread_events[0].session_id == memory.session.id


async def test_update_thread_rejects_unknown_or_foreign_and_saves_nothing_while_paused(
    world: World,
) -> None:
    other = make_user()
    world.state.users[other.id] = other
    foreign = make_thread(other.id)
    own = make_thread(world.user.id)
    for thread in (foreign, own):
        world.state.threads[thread.id] = thread
    memory = await world.service.open_call(world.user)

    assert await memory.update_thread("missing", event="x") is None
    assert await memory.update_thread(foreign.id, event="x") is None
    memory.pause_saving()
    assert await memory.update_thread(own.id, event="x") is None
    assert await memory.track_thread("x") is None
    assert world.state.thread_events == []
    assert world.state.threads == {foreign.id: foreign, own.id: own}


async def test_set_boundary_scopes(world: World) -> None:
    own = make_thread(world.user.id)
    world.state.threads[own.id] = own
    memory = await world.service.open_call(world.user)

    today = await memory.set_boundary("painting", BoundaryScope.THIS_CALL)
    dated = await memory.set_boundary("doctor", BoundaryScope.UNTIL_DATE, at(60), own.id)
    undated = await memory.set_boundary("almari", BoundaryScope.UNTIL_DATE)
    foreign_thread = await memory.set_boundary("x", thread_id="missing")

    assert (today.scope, today.session_id) == (BoundaryScope.THIS_CALL, memory.session.id)
    assert (dated.scope, dated.until, dated.thread_id) == (
        BoundaryScope.UNTIL_DATE,
        at(60),
        own.id,
    )
    assert (undated.scope, undated.until) == (BoundaryScope.UNTIL_REOPENED, None)
    assert foreign_thread.thread_id is None
    assert len(world.state.boundaries) == 4


async def test_lift_boundary_only_own_active_ones(world: World) -> None:
    other = make_user()
    world.state.users[other.id] = other
    foreign = make_boundary(other.id)
    world.state.boundaries[foreign.id] = foreign
    memory = await world.service.open_call(world.user)
    painting = await memory.set_boundary("painting")

    assert await memory.lift_boundary(painting.id)
    assert not await memory.lift_boundary(painting.id)
    assert not await memory.lift_boundary(foreign.id)
    assert not await memory.lift_boundary("missing")
    assert world.state.boundaries[painting.id].lifted_at == world.clock.now()
    assert (await memory.context()).boundaries == []


async def test_note_declined_redacts_and_links_the_call(world: World) -> None:
    memory = await world.service.open_call(world.user)

    declined = await memory.note_declined("Go for a walk")

    assert world.state.suggestions == [declined]
    assert declined.session_id == memory.session.id


async def test_privacy_mode_is_off_until_enabled(world: World) -> None:
    memory = await world.service.open_call(world.user)
    assert not memory.private

    memory.enable_privacy_mode()

    assert memory.private


async def test_forget_thread_needs_confirmation_then_erases_it_and_its_calls(world: World) -> None:
    last_call = make_session(world.user.id)
    world.state.sessions[last_call.id] = last_call
    world.state.turns.append(make_turn(last_call.id, 1, "Ramesh se baat karni hai"))
    memory = await world.service.open_call(world.user)
    thread = await memory.track_thread("Reconnect with Ramesh", about="Ramesh")
    assert thread is not None
    world.state.threads[thread.id] = replace(thread, source_session_id=last_call.id)
    await memory.update_thread(thread.id, event="Called him")
    memory.record(Role.USER, "Ramesh wali baat bhool jao poori tarah")

    assert await memory.forget_thread(thread.id, user_turn=1) is ForgetOutcome.NEEDS_CONFIRMATION
    assert thread.id in world.state.threads
    assert await memory.forget_thread(thread.id, user_turn=2) is ForgetOutcome.DELETED

    assert world.state.threads == {}
    assert world.state.thread_events == []
    assert world.state.people == {}
    assert {t.text for t in world.state.turns} == {FORGOTTEN_TURN}
    await memory.close()
    assert not world.state.sessions[memory.session.id].ended_abruptly
    assert await memory.forget_thread(thread.id, user_turn=3) is ForgetOutcome.NOT_FOUND


async def test_forget_thread_rejects_foreign_threads(world: World) -> None:
    other = make_user()
    world.state.users[other.id] = other
    foreign = make_thread(other.id)
    world.state.threads[foreign.id] = foreign
    memory = await world.service.open_call(world.user)

    assert await memory.forget_thread(foreign.id, user_turn=1) is ForgetOutcome.NOT_FOUND
    assert await memory.forget_thread(foreign.id, user_turn=2) is ForgetOutcome.NOT_FOUND
    assert foreign.id in world.state.threads


async def test_a_person_stays_while_a_memory_or_storyline_is_about_them(world: World) -> None:
    memory = await world.service.open_call(world.user)
    fact = await memory.remember("Misses Ramesh", about="Ramesh")
    thread = await memory.track_thread("Reconnect with Ramesh", about="Ramesh")
    assert fact is not None
    assert thread is not None

    await memory.forget_thread(thread.id, user_turn=1)
    await memory.forget_thread(thread.id, user_turn=2)
    assert [p.name for p in world.state.people.values()] == ["Ramesh"]

    second = await memory.track_thread("Meet Ramesh", about="Ramesh")
    assert second is not None
    await memory.forget(fact.id, user_turn=3)
    await memory.forget(fact.id, user_turn=4)
    assert [p.name for p in world.state.people.values()] == ["Ramesh"]


async def test_set_preferences_changes_only_given_fields_and_persists(world: World) -> None:
    user = replace(world.user, preferences=Preferences(address_as="Mohan ji", fewer_questions=True))
    world.state.users[user.id] = user
    memory = await world.service.open_call(user)
    memory.pause_saving()

    first = await memory.set_preferences(pace=Pace.SLOW, verbosity=Verbosity.SHORT)
    second = await memory.set_preferences(address_as="  Bauji ", fewer_questions=False)

    assert first == Preferences("Mohan ji", Pace.SLOW, Verbosity.SHORT, True)
    assert second == Preferences("Bauji", Pace.SLOW, Verbosity.SHORT, False)
    assert memory.preferences == second
    assert world.state.users[user.id].preferences == second


# T0 is Thu 1 Jan 2026, 2:30 pm in India.


async def test_create_reminder_with_and_without_a_date(world: World) -> None:
    memory = await world.service.open_call(world.user)
    memory.pause_saving()

    birthday = await memory.create_reminder(
        "Ravi's birthday",
        on=date(2026, 11, 18),
        repeat=Repeat.YEARLY,
        alert_days_before=(0, 7, 7, 90, -1),
        about="Ravi",
        note="Call him OTP 123456",
    )
    trip = await memory.create_reminder("Book the June trip")

    assert world.state.reminders[birthday.id] == birthday
    assert (birthday.status, birthday.at, birthday.alert_days_before) == (
        ReminderStatus.ACTIVE,
        time(9, 0),
        (7, 0),
    )
    assert birthday.note == "Call him OTP [redacted]"
    assert world.state.people[birthday.about_person_id or ""].name == "Ravi"
    assert birthday.source_session_id == memory.session.id
    assert (trip.status, trip.alert_days_before) == (ReminderStatus.NEEDS_INFO, (0,))
    nothing = await memory.create_reminder("x", alert_days_before=(-3,))
    assert nothing.alert_days_before == (0,)


async def test_update_reminder_changes_the_same_reminder(world: World) -> None:
    """Scenario #43: "purani ko hi badal do, do alag-alag mat banana"."""
    memory = await world.service.open_call(world.user)
    trip = await memory.create_reminder("Book the June trip")
    glasses = await memory.create_reminder("Pick up glasses", on=date(2026, 1, 3))

    dated = await memory.update_reminder(trip.id, on=date(2026, 6, 22))
    moved = await memory.update_reminder(
        glasses.id, on=date(2026, 1, 5), at=time(11), title="Glasses", alert_days_before=(1,)
    )
    noted = await memory.update_reminder(glasses.id, note="Shop opens at 11", repeat=Repeat.ONCE)

    assert dated is not None
    assert (dated.status, dated.on) == (ReminderStatus.ACTIVE, date(2026, 6, 22))
    assert moved is not None
    assert (moved.title, moved.on, moved.at, moved.alert_days_before) == (
        "Glasses",
        date(2026, 1, 5),
        time(11),
        (1,),
    )
    assert noted is not None
    assert (noted.note, noted.on) == ("Shop opens at 11", date(2026, 1, 5))
    assert len(world.state.reminders) == 2
    assert await memory.update_reminder("missing", title="x") is None
    still_undated = await memory.create_reminder("Undated")
    renamed = await memory.update_reminder(still_undated.id, title="Still undated")
    assert renamed is not None
    assert renamed.status is ReminderStatus.NEEDS_INFO


async def test_skip_one_occurrence_keeps_the_series(world: World) -> None:
    """Scenario #44: first Sunday of every month, "sirf is baar skip kar do"."""
    memory = await world.service.open_call(world.user)
    sundays = await memory.create_reminder(
        "Family video call", on=date(2025, 12, 7), repeat=Repeat.MONTHLY_WEEKDAY
    )

    skipped = await memory.settle_occurrence(sundays.id, OccurrenceStatus.SKIPPED)

    assert skipped == date(2026, 1, 4)
    assert world.state.occurrences == {(sundays.id, date(2026, 1, 4)): OccurrenceStatus.SKIPPED}
    assert world.state.reminders[sundays.id].status is ReminderStatus.ACTIVE
    context = await memory.context()
    assert context.reminders == []


async def test_completing_a_one_off_reminder_ends_it(world: World) -> None:
    """Scenario #42: "bill bhar diya; baaki reminder hata do"."""
    memory = await world.service.open_call(world.user)
    bill = await memory.create_reminder(
        "Pay electricity bill", on=date(2026, 1, 5), alert_days_before=(3, 0)
    )
    glasses = await memory.create_reminder("Pick up glasses", on=date(2026, 1, 2))

    assert await memory.settle_occurrence(bill.id, OccurrenceStatus.DONE) == date(2026, 1, 5)
    assert world.state.reminders[bill.id].status is ReminderStatus.DONE
    assert await memory.settle_occurrence(bill.id, OccurrenceStatus.DONE) is None
    assert await memory.settle_occurrence(
        glasses.id, OccurrenceStatus.SKIPPED, on=date(2026, 1, 2)
    ) == date(2026, 1, 2)
    assert world.state.reminders[glasses.id].status is ReminderStatus.CANCELLED
    undated = await memory.create_reminder("Someday")
    assert await memory.settle_occurrence(undated.id, OccurrenceStatus.DONE) is None


async def test_cancel_reminder_needs_confirmation(world: World) -> None:
    memory = await world.service.open_call(world.user)
    walk = await memory.create_reminder("Evening walk", on=date(2026, 1, 2), repeat=Repeat.WEEKLY)

    assert await memory.cancel_reminder(walk.id, user_turn=1) is ForgetOutcome.NEEDS_CONFIRMATION
    assert world.state.reminders[walk.id].status is ReminderStatus.ACTIVE
    assert await memory.cancel_reminder(walk.id, user_turn=2) is ForgetOutcome.DELETED
    assert world.state.reminders[walk.id].status is ReminderStatus.CANCELLED
    assert await memory.cancel_reminder(walk.id, user_turn=3) is ForgetOutcome.NOT_FOUND


async def test_reminders_owned_by_someone_else_are_not_found(world: World) -> None:
    other = make_user()
    world.state.users[other.id] = other
    foreign = make_reminder(other.id, on=date(2026, 1, 2))
    world.state.reminders[foreign.id] = foreign
    memory = await world.service.open_call(world.user)

    assert await memory.update_reminder(foreign.id, title="x") is None
    assert await memory.settle_occurrence(foreign.id, OccurrenceStatus.DONE) is None
    assert await memory.cancel_reminder(foreign.id, user_turn=1) is ForgetOutcome.NOT_FOUND


async def test_context_brings_up_alerted_reminders_first(world: World) -> None:
    """Scenario #34: the call after the alert brings Ravi's birthday up."""
    last = make_session(world.user.id, started_at=world.clock.now() - timedelta(days=10))
    world.state.sessions[last.id] = last
    memory = await world.service.open_call(world.user)
    birthday = await memory.create_reminder(
        "Ravi's birthday", on=date(2026, 1, 7), repeat=Repeat.YEARLY, alert_days_before=(7, 0)
    )
    dentist = await memory.create_reminder("Dentist", on=date(2026, 1, 3))
    later = await memory.create_reminder("Far away", on=date(2026, 3, 1))
    undated = await memory.create_reminder("June trip")

    context = await memory.context()

    assert [(d.reminder.id, d.on, d.alerted) for d in context.reminders] == [
        (birthday.id, date(2026, 1, 7), True),
        (dentist.id, date(2026, 1, 3), False),
    ]
    assert context.undated_reminders == [undated]
    assert later.id not in {d.reminder.id for d in context.reminders}
    # Listed separately, so "18 nahi, 19 November" can change it (found in a scenario eval).
    assert context.later_reminders == [later]


async def test_reminder_people_are_kept_until_nothing_refers_to_them(world: World) -> None:
    memory = await world.service.open_call(world.user)
    fact = await memory.remember("Ravi loves cricket", about="Ravi")
    await memory.create_reminder("Ravi's birthday", about="Ravi")
    assert fact is not None

    await memory.forget(fact.id, user_turn=1)
    await memory.forget(fact.id, user_turn=2)

    assert [p.name for p in world.state.people.values()] == ["Ravi"]


async def test_a_call_searches_with_its_languages_cut_off() -> None:
    world = build_world()
    seen: list[float | None] = []
    search = world.store.search

    async def recording(user_id: str, query: str, limit: int, min_similarity: float | None = None):  # type: ignore[no-untyped-def]
        seen.append(min_similarity)
        return await search(user_id, query, limit, min_similarity)

    world.store.search = recording  # type: ignore[method-assign]
    cut_offs = {"english": 0.2, None: 0.12}
    service = MemoryService(
        world.deps, world.embedder, lambda language: cut_offs.get(language, 0.3)
    )
    english = replace(world.user, language="english")

    memory = await service.open_call(english)
    await memory.recall("my interview")  # the user's own words
    await memory.search("interview")  # the recall tool's keywords
    hinglish_call = await service.open_call(world.user, language="hinglish")
    await hinglish_call.recall("mera interview")

    assert seen == [0.2, 0.12, 0.3]


async def duplicate_call(world: World) -> CallMemory:
    service = MemoryService(world.deps, world.embedder, duplicate_at=0.8)
    return await service.open_call(world.user)


async def test_remember_doesnt_save_a_near_copy(world: World) -> None:
    memory = await duplicate_call(world)
    first = await memory.remember("Wife Sabitri is 54")

    again = await memory.remember("Wife Sabitri is 54 years old")

    assert isinstance(first, Fact)
    assert again == AlreadySaved([first])
    assert len(await world.store.active(world.user.id, 10)) == 1


async def test_remember_saves_a_close_but_different_memory_when_told(world: World) -> None:
    memory = await duplicate_call(world)
    await memory.remember("Wife Sabitri is 54")

    saved = await memory.remember("Wife Sabitri is 54 years old", different=True)

    assert isinstance(saved, Fact)
    assert len(await world.store.active(world.user.id, 10)) == 2


@pytest.mark.parametrize(
    ("name", "relation", "expected"),
    [
        ("Ramesh (college friend)", None, ("Ramesh", "college friend")),
        (" Ramesh ( college friend ) ", "friend", ("Ramesh", "friend")),
        ("Ramesh", "college friend", ("Ramesh", "college friend")),
        ("  Ramesh  ", None, ("Ramesh", None)),
    ],
)
def test_split_relation(name: str, relation: str | None, expected: tuple[str, str | None]) -> None:
    assert split_relation(name, relation) == expected


async def test_a_relation_in_the_name_doesnt_make_a_second_person(world: World) -> None:
    memory = await world.service.open_call(world.user)

    first = await memory.person("Ramesh", "college friend")
    second = await memory.person("Ramesh (college friend)")

    assert second.id == first.id
