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
from baatein.voice.languages import HINGLISH
from tests.factories import make_followup
from tests.fakes.world import build_world

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


async def test_saves_followup_for_upcoming_event() -> None:
    world = build_world()
    memory = await world.service.open_call(world.user)
    context = render_context(await memory.context())
    model = Settings().llm.model
    async with JudgeLLM(model=model) as llm, AgentSession(llm=llm) as session:
        await session.start(Assistant(HINGLISH, memory=memory, memory_context=context))

        result = await session.run(
            user_input="Agle Friday mera Google mein interview hai, thoda nervous hoon."
        )

        result.expect.contains_function_call(name="add_followup")
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
