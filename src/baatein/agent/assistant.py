from livekit.agents import Agent, llm

from baatein.agent.tools import memory_tools
from baatein.memory.context_builder import render_recall
from baatein.memory.service import CallMemory
from baatein.voice.languages import LanguageProfile

BASE_INSTRUCTIONS = """\
You are Baatein, a warm and curious voice companion who remembers people and \
keeps conversations moving.

Your replies are spoken aloud, so:
- Keep each reply to one to three short sentences.
- Never use markdown, lists, emojis, or any formatting.
- Ask at most one question at a time, and build on what the user just said.
"""

MEMORY_INSTRUCTIONS = """\
You remember this user across calls. When they mention something coming up \
(an interview, exam, trip, appointment), call add_followup so you can ask about it \
next time. Never say that you are saving or looking things up.
"""


def build_instructions(
    profile: LanguageProfile, user_name: str | None = None, memory_context: str | None = None
) -> str:
    instructions = f"{BASE_INSTRUCTIONS}\nLanguage: {profile.reply_style}\n"
    if user_name:
        instructions += f"\nYou are talking with {user_name}. Use their name naturally.\n"
    if memory_context is not None:
        instructions += f"\n{MEMORY_INSTRUCTIONS}\n{memory_context}\n"
    return instructions


class Assistant(Agent):
    def __init__(
        self,
        profile: LanguageProfile,
        user_name: str | None = None,
        memory: CallMemory | None = None,
        memory_context: str | None = None,
    ) -> None:
        super().__init__(
            instructions=build_instructions(profile, user_name, memory_context),
            tools=memory_tools(memory) if memory else None,
        )
        self._memory = memory

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        if self._memory is None or not (text := new_message.text_content):
            return
        recalled = render_recall(await self._memory.recall(text))
        if recalled:
            turn_ctx.add_message(role="system", content=recalled)
