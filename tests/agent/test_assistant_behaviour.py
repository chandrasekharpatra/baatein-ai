import os
import re
from typing import Any

import pytest
from livekit.agents import NOT_GIVEN, AgentSession, NotGivenOr
from livekit.agents.llm import LLMStream
from livekit.agents.voice.run_result import ChatMessageEvent, RunResult
from livekit.plugins import anthropic

from baatein.agent.assistant import Assistant
from baatein.config import Settings
from baatein.domain.models import Pace, ThreadStatus
from baatein.memory.context_builder import render_context
from baatein.memory.service import CallMemory
from baatein.voice.languages import HINGLISH
from baatein.voice.personas import CASUAL, ELDER, Persona
from tests.factories import at, make_boundary, make_fact, make_session, make_thread
from tests.fakes.world import World, build_world

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
]


class JudgeLLM(anthropic.LLM):
    def __init__(self, **kwargs: Any) -> None:
        # Same tool schemas as the agent: see voice.providers.anthropic_llm.
        super().__init__(_strict_tool_schema=False, **kwargs)

    def chat(
        self, *, extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN, **kwargs: Any
    ) -> LLMStream:
        # LiveKit's judge always sends temperature=0.0, which Claude 5.5 rejects.
        extra = {k: v for k, v in (extra_kwargs or {}).items() if k != "temperature"}
        return super().chat(extra_kwargs=extra, **kwargs)


async def test_replies_in_short_hinglish() -> None:
    model = Settings().llm.model
    async with JudgeLLM(model=model) as llm, AgentSession(llm=llm) as session:
        await session.start(Assistant(HINGLISH))

        result = await session.run(user_input="Hi yaar, aaj office mein bahut kaam tha.")

        await (
            result.expect.next_event()
            .is_message(role="assistant")
            .judge(
                llm,
                intent=(
                    "Responds empathetically in casual Hinglish written in Roman script, "
                    "in at most three short sentences, without markdown or emojis."
                ),
            )
        )
        result.expect.no_more_events()


def with_ramesh_storyline(world: World) -> str:
    """A storyline from a call two days ago (#1)."""
    last_call = make_session(world.user.id, started_at=at(-2 * 24 * 60))
    world.state.sessions[last_call.id] = last_call
    thread = make_thread(
        world.user.id,
        "Reconnect with college friend Ramesh by phone",
        source_session_id=last_call.id,
    )
    world.state.threads[thread.id] = thread
    return thread.id


def assistant_replies(result: RunResult[None]) -> list[str]:
    return [
        event.item.text_content or ""
        for event in result.events
        if isinstance(event, ChatMessageEvent) and event.item.role == "assistant"
    ]


async def start_with_memory(
    session: AgentSession[None],
    world: World,
    user_name: str | None = None,
    persona: Persona = CASUAL,
) -> CallMemory:
    memory = await world.service.open_call(world.user)
    context = render_context(await memory.context())
    await session.start(
        Assistant(
            HINGLISH, user_name=user_name, memory=memory, memory_context=context, persona=persona
        )
    )
    return memory


async def test_asks_before_saving_and_saves_after_yes() -> None:
    world = build_world()
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world, user_name="Mohan")

        first = await session.run(
            user_input="Aaj college ke din yaad aa rahe the. Mera ek dost tha, Ramesh. Hum har "
            "shaam saath chai peete the. Pachees saal ho gaye, kabhi sochta hoon phone karun."
        )
        assert await world.store.active(world.user.id, limit=5) == []
        await first.expect.contains_message(role="assistant").judge(
            llm, intent="Responds warmly and asks whether it may remember this about Ramesh."
        )

        await session.run(user_input="Haan, yaad rakhna.")

        [fact] = await world.store.active(world.user.id, limit=5)
        assert "ramesh" in fact.text.lower()


async def test_saves_directly_when_the_user_asks_to_remember() -> None:
    world = build_world()
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world, user_name="Asha")

        await session.run(
            user_input="Ek baat yaad rakhoge? Mere bete Ravi ka birthday 18 November ko hai."
        )

        [fact] = await world.store.active(world.user.id, limit=5)
        assert "ravi" in fact.text.lower()
        assert [p.name for p in world.state.people.values()] == ["Ravi"]


async def test_saves_nothing_after_user_says_not_to() -> None:
    world = build_world()
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        memory = await start_with_memory(session, world)

        await session.run(
            user_input="Aaj gardening par baat kar sakte hain, lekin kuch bhi save mat karna."
        )
        await session.run(
            user_input="Mere balcony mein tulsi aur gulab hain, roz subah unhe paani deti hoon."
        )

        assert memory.saving_paused
        assert await world.store.active(world.user.id, limit=5) == []


async def test_forgets_on_request_and_does_not_use_it_again() -> None:
    world = build_world()
    await world.store.add(
        make_fact(
            world.user.id, "Is worried about a dispute with his cousin Mahesh over the Pune flat"
        )
    )
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world)

        asked = await session.run(
            user_input="Maine jo family wali pareshani batayi thi, use bhool jao."
        )
        [fact] = list(world.store._facts.values())
        assert not fact.is_deleted
        await asked.expect.contains_message(role="assistant").judge(
            llm, intent="Asks the user to confirm before deleting the family matter."
        )

        await session.run(user_input="Haan, poori tarah mita do.")
        [fact] = list(world.store._facts.values())
        assert fact.is_deleted

        result = await session.run(user_input="Tumhe meri family wali baat yaad hai?")
        await result.expect.contains_message(role="assistant").judge(
            llm,
            intent="Says it no longer has that family matter saved (or that it was deleted), "
            "without mentioning Mahesh, a cousin, Pune or a flat.",
        )


async def test_offers_to_check_in_and_tracks_it_after_yes() -> None:
    world = build_world()
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world)

        await session.run(
            user_input="Agle Friday mera Google mein interview hai, thoda nervous hoon."
        )
        await session.run(user_input="Haan, next time zaroor poochna kaisa gaya.")

        [thread] = world.state.threads.values()
        assert "interview" in thread.title.lower()


async def test_checks_in_once_and_records_how_the_story_moved() -> None:
    """Scenario #1: the next call asks about Ramesh, and "maine phone kar diya" is logged."""
    world = build_world()
    thread_id = with_ramesh_storyline(world)
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world, user_name="Mohan")

        replies = assistant_replies(await session.run(user_input="Namaste Baatein!"))
        if not any("ramesh" in reply.lower() for reply in replies):
            replies += assistant_replies(await session.run(user_input="Main theek hoon."))
        assert sum("ramesh" in reply.lower() for reply in replies) == 1, replies

        await session.run(user_input="Haan! Maine unko phone kar diya, bahut achha laga.")

        assert world.state.thread_events, "update_thread was not called"
        assert world.state.threads[thread_id].status in (
            ThreadStatus.DONE,
            ThreadStatus.IN_PROGRESS,
        )


async def test_accepts_no_to_a_check_in() -> None:
    world = build_world()
    with_ramesh_storyline(world)
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world, user_name="Mohan")

        await session.run(user_input="Namaste Baatein!")
        result = await session.run(user_input="Nahi, abhi uske baare mein baat nahi karni.")

        await result.expect.contains_message(role="assistant").judge(
            llm,
            intent="Accepts that without pressure or guilt and moves on; does not ask about "
            "Ramesh again.",
        )


async def test_respects_a_boundary_and_lifts_it_when_reopened() -> None:
    """Scenario #11: "aaj painting yaad nahi dilana", later "ab poochh sakte ho"."""
    world = build_world()
    await world.store.add(make_fact(world.user.id, "Is painting a landscape of the village"))
    thread = make_thread(world.user.id, "Finish the village painting")
    boundary = make_boundary(world.user.id, "painting")
    world.state.threads[thread.id] = thread
    world.state.boundaries[boundary.id] = boundary
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world)

        result = await session.run(user_input="Hi Baatein, aaj kuch halki-phulki baat karte hain.")
        await result.expect.contains_message(role="assistant").judge(
            llm, intent="Replies warmly without mentioning painting or the village landscape."
        )

        await session.run(user_input="Ab painting ke baare mein poochh sakte ho.")

        assert world.state.boundaries[boundary.id].lifted_at is not None


async def test_sets_a_boundary_when_asked() -> None:
    world = build_world()
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world)

        await session.run(user_input="Almari saaf karne ke baare mein mujhse mat poochhna.")

        [boundary] = world.state.boundaries.values()
        assert "almari" in boundary.topic.lower() or "cupboard" in boundary.topic.lower()


async def test_forgets_a_storyline_only_after_confirmation() -> None:
    world = build_world()
    thread_id = with_ramesh_storyline(world)
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world, user_name="Mohan")

        await session.run(user_input="Ramesh wali baat poori tarah bhool jao.")
        assert thread_id in world.state.threads

        await session.run(user_input="Haan, mita do.")
        assert thread_id not in world.state.threads


DEVANAGARI = re.compile("[\u0900-\u097f]")


async def test_elder_persona_is_respectful_and_brief() -> None:
    world = build_world()
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world, user_name="Mohan", persona=ELDER)

        result = await session.run(user_input="Namaste beta. Aaj ghutno mein thoda dard tha.")

        [reply] = assistant_replies(result)
        assert re.search(r"\bji\b", reply, re.IGNORECASE), reply
        assert reply.count("?") <= 1, reply
        await result.expect.contains_message(role="assistant").judge(
            llm,
            intent="Replies respectfully to Mohan ji (respectful forms such as 'aap' or "
            "'dikhaiye', never 'tum'), mostly in Hindi, in short simple sentences, with sympathy "
            "and at most one question.",
        )


async def test_replies_in_roman_script_to_devanagari_input() -> None:
    world = build_world()
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world)

        result = await session.run(user_input="आज मैंने अपनी बेटी से बहुत देर तक बात की।")

        [reply] = assistant_replies(result)
        assert not DEVANAGARI.search(reply), reply


async def test_fewer_questions_when_asked_and_remembered() -> None:
    """Scenario #21: "Arre, itne sawaal mat poochho"."""
    world = build_world()
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world, user_name="Kamala")

        await session.run(user_input="Arre, itne sawaal mat poochho, bas suno.")
        assert world.state.users[world.user.id].preferences.fewer_questions

        result = await session.run(
            user_input="Aaj bazaar gayi thi, sabzi bahut mehngi ho gayi hai aajkal."
        )
        [reply] = assistant_replies(result)
        assert reply.count("?") <= 1, reply


async def test_slower_speech_when_asked() -> None:
    """Scenario #23: "Dheere bolo, sunai nahi diya"."""
    world = build_world()
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world)

        await session.run(user_input="Thoda dheere bolo, sunai nahi diya.")

        assert world.state.users[world.user.id].preferences.pace is Pace.SLOW
