import os

import pytest
from livekit.agents import AgentSession
from livekit.plugins import anthropic

from baatein.agent.assistant import Assistant
from baatein.config import Settings
from baatein.voice.languages import HINGLISH

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
]


async def test_replies_in_short_hinglish() -> None:
    model = Settings().llm.model
    async with anthropic.LLM(model=model) as llm, AgentSession(llm=llm) as session:
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
