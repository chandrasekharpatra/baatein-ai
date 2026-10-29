import asyncio
import logging
from datetime import timedelta

import pytest

from baatein.config import MemorySettings
from baatein.domain.models import FinalizeStatus, FollowUpStatus, Role, ScoredFact
from baatein.memory.service import CallMemory
from tests.factories import at, make_fact, make_followup, make_session, make_turn, make_user
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


async def test_context_loads_summaries_followups_and_facts(world: World) -> None:
    user_id = world.user.id
    older = make_session(user_id, started_at=at(-3000), summary="Talked about exams.")
    unsummarized = make_session(user_id, started_at=at(-2000))
    other_user = make_user()
    world.state.users[other_user.id] = other_user
    foreign = make_session(other_user.id, summary="Not theirs.")
    for session in (older, unsummarized, foreign):
        world.state.sessions[session.id] = session
    followup = make_followup(user_id, text="Ask about the interview")
    world.state.followups[followup.id] = followup
    await world.store.add(make_fact(user_id, "Works at Infosys", importance=5))

    memory = await world.service.open_call(world.user)
    context = await memory.context()

    assert context.today == world.clock.now().date()
    assert context.summaries == [older]
    assert context.followups == [followup]
    assert [f.text for f in context.facts] == ["Works at Infosys"]


async def test_context_respects_limits() -> None:
    world = build_world(MemorySettings(context_summaries=1, context_followups=1, context_facts=1))
    for minutes in (-30, -20, -10):
        session = make_session(world.user.id, started_at=at(minutes), summary=f"s{minutes}")
        world.state.sessions[session.id] = session
        followup = make_followup(world.user.id, text=f"f{minutes}")
        world.state.followups[followup.id] = followup
        await world.store.add(make_fact(world.user.id, f"fact {minutes}"))

    context = await (await world.service.open_call(world.user)).context()

    assert [s.summary for s in context.summaries] == ["s-10"]
    assert len(context.followups) == 1
    assert len(context.facts) == 1


async def test_recall_returns_relevant_facts(world: World) -> None:
    await world.store.add(make_fact(world.user.id, "Has a job interview at Google"))
    await world.store.add(make_fact(world.user.id, "Loves masala chai"))
    memory = await world.service.open_call(world.user)

    recalled = await memory.recall("google interview")

    assert recalled[0].fact.text == "Has a job interview at Google"


async def test_recall_gives_up_after_timeout(caplog: pytest.LogCaptureFixture) -> None:
    world = build_world(MemorySettings(recall_timeout_seconds=0.01))

    async def slow(*_: object) -> list[ScoredFact]:
        await asyncio.sleep(1)
        return []

    world.store.search = slow  # type: ignore[method-assign]
    memory = await world.service.open_call(world.user)

    assert await memory.recall("anything") == []
    assert "timed out" in caplog.text


async def test_recall_swallows_errors(world: World, caplog: pytest.LogCaptureFixture) -> None:
    async def broken(*_: object) -> list[ScoredFact]:
        raise RuntimeError("db locked")

    world.store.search = broken  # type: ignore[method-assign]
    memory = await world.service.open_call(world.user)

    with caplog.at_level(logging.ERROR):
        assert await memory.recall("anything") == []
    assert "memory recall failed" in caplog.text


async def test_search_finds_facts_and_earlier_turns_only(world: World) -> None:
    await world.store.add(make_fact(world.user.id, "Sister Priya is getting married"))
    earlier = make_session(world.user.id)
    world.state.sessions[earlier.id] = earlier
    world.state.turns.append(make_turn(earlier.id, 1, "Priya ki shaadi December mein hai"))
    memory = await world.service.open_call(world.user)
    world.state.turns.append(make_turn(memory.session.id, 1, "Priya ki baat karte hain"))

    result = await memory.search("Priya")

    assert [s.fact.text for s in result.facts] == ["Sister Priya is getting married"]
    assert [t.text for t in result.turns] == ["Priya ki shaadi December mein hai"]


async def test_add_followup_links_session_and_due_date(world: World) -> None:
    memory = await world.service.open_call(world.user)

    followup = await memory.add_followup("Ask how the interview went", at(60 * 24))

    stored = world.state.followups[followup.id]
    assert stored.user_id == world.user.id
    assert stored.source_session_id == memory.session.id
    assert stored.due_at == at(60 * 24)
    assert stored.status is FollowUpStatus.OPEN


async def test_resolve_followup_closes_own_open_followup(world: World) -> None:
    followup = make_followup(world.user.id)
    world.state.followups[followup.id] = followup
    memory = await world.service.open_call(world.user)

    resolved = await memory.resolve_followup(followup.id, FollowUpStatus.DONE)

    assert resolved is not None
    assert resolved.status is FollowUpStatus.DONE
    assert resolved.resolved_at == world.clock.now()


async def test_resolve_followup_rejects_unknown_foreign_or_closed(world: World) -> None:
    other = make_user()
    world.state.users[other.id] = other
    foreign = make_followup(other.id)
    closed = make_followup(world.user.id, status=FollowUpStatus.DONE)
    for followup in (foreign, closed):
        world.state.followups[followup.id] = followup
    memory: CallMemory = await world.service.open_call(world.user)

    assert await memory.resolve_followup("missing", FollowUpStatus.DONE) is None
    assert await memory.resolve_followup(foreign.id, FollowUpStatus.DONE) is None
    assert await memory.resolve_followup(closed.id, FollowUpStatus.DROPPED) is None
    assert world.state.followups[foreign.id].status is FollowUpStatus.OPEN
