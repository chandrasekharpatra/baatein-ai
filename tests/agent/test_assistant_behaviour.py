import os
from typing import Any

import pytest
from livekit.agents import NOT_GIVEN, AgentSession, NotGivenOr
from livekit.agents.llm import LLMStream
from livekit.plugins import anthropic

from baatein.agent.assistant import Assistant
from baatein.config import Settings
from baatein.domain.models import FollowUpStatus
from baatein.memory.context_builder import render_context
from baatein.memory.service import CallMemory
from baatein.voice.languages import HINGLISH
from tests.factories import make_fact, make_followup
from tests.fakes.world import World, build_world

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
]


class JudgeLLM(anthropic.LLM):
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


async def start_with_memory(
    session: AgentSession[None], world: World, user_name: str | None = None
) -> CallMemory:
    memory = await world.service.open_call(world.user)
    context = render_context(await memory.context())
    await session.start(
        Assistant(HINGLISH, user_name=user_name, memory=memory, memory_context=context)
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


async def test_offers_followup_and_saves_it_after_yes() -> None:
    world = build_world()
    async with JudgeLLM(model=Settings().llm.model) as llm, AgentSession(llm=llm) as session:
        await start_with_memory(session, world)

        await session.run(
            user_input="Agle Friday mera Google mein interview hai, thoda nervous hoon."
        )
        await session.run(user_input="Haan, next time zaroor poochna kaisa gaya.")

        [followup] = world.state.followups.values()
        assert "interview" in followup.text.lower()


async def test_follows_up_on_open_followup_and_resolves_it() -> None:
    world = build_world()
    followup = make_followup(world.user.id, text="Ask how the Google interview went")
    world.state.followups[followup.id] = followup
    memory = await world.service.open_call(world.user)
    context = render_context(await memory.context())
    model = Settings().llm.model
    async with JudgeLLM(model=model) as llm, AgentSession(llm=llm) as session:
        await session.start(
            Assistant(HINGLISH, user_name="Asha", memory=memory, memory_context=context)
        )

        greeting = await session.run(user_input="Hi Baatein!")
        await greeting.expect.contains_message(role="assistant").judge(
            llm, intent="Greets Asha and asks how her Google interview went."
        )

        await session.run(user_input="Interview accha gaya, next round ke liye select ho gayi!")

        assert world.state.followups[followup.id].status is FollowUpStatus.DONE
