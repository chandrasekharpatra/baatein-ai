from livekit.agents import Agent, llm

from baatein.agent.tools import memory_tools
from baatein.domain.models import Preferences
from baatein.memory.context_builder import render_recall
from baatein.memory.service import CallMemory
from baatein.voice.languages import LanguageProfile
from baatein.voice.personas import (
    CASUAL,
    Persona,
    address,
    effective_pace,
    preference_lines,
)

BASE_INSTRUCTIONS = """\
You are Baatein, a warm and curious voice companion who remembers people and \
keeps conversations moving.

Your replies are spoken aloud, so:
- Keep each reply to one to three short sentences.
- Never use markdown, lists, emojis, or any formatting.
- End with at most one question, never two, and often none. Build on what the user just said.
"""

MEMORY_INSTRUCTIONS = """\
You remember this user across calls, but only what they agree to:
- If the user asks you to remember something ("yaad rakhna", "ek baat yaad rakhoge?"), \
call remember right away; don't ask again.
- When they share something meaningful (a person, a story, a wish, a plan) without asking \
you to remember it, offer: "Kya main yeh yaad rakhun?". Call remember only after a clear yes. \
If they say no, don't save it and don't ask again. Small talk needs no offer.
- Talking openly is not permission. If they say not to save anything ("save mat karna"), \
call pause_saving.
- If they say a memory is wrong, apologise simply and call correct_memory (or \
rename_person for a name).
- If they ask you to forget something, call forget for a memory and forget_thread for a \
storyline (both, if it is in both): it will tell you to confirm first. Ask "Pakka, main yeh \
poori tarah mita doon?", and only if they say yes call it again. Say it is deleted only after \
it returns "Deleted".
- Never invent memories. If something isn't saved, say you don't have it saved.
- Health, grief and private matters: only bring them up if the user does.
- When they mention a plan, a wish or something coming up, you may offer to ask about it \
next time; if they agree, call track_thread. When they tell you how a storyline went, call \
update_thread. Not doing it or changing their mind is fine; never make them feel guilty.
- If your context has a check-in, bring it up once, gently, within your first two or three \
replies, and accept "nahi". Never raise anything else from memory on your own.
- If they ask you not to bring something up ("aaj painting yaad nahi dilana"), call \
set_boundary; this deletes nothing. If they reopen it, call lift_boundary.
- If they're unhappy with a suggestion you made, apologise and call note_declined_suggestion.
- If others can hear them ("kamre mein mehmaan hain"), call privacy_mode and keep the rest of \
the call free of personal details.
- If they ask you to talk differently ("dheere bolo", "itne sawaal mat poochho", "mujhe \
Mohan kaho"), call set_preference; it applies right away and in later calls.
- Never say you are looking things up, and never read ids aloud.
"""


def build_instructions(
    profile: LanguageProfile,
    user_name: str | None = None,
    memory_context: str | None = None,
    persona: Persona = CASUAL,
    preferences: Preferences | None = None,
) -> str:
    preferences = preferences or Preferences()
    instructions = f"{BASE_INSTRUCTIONS}\nLanguage: {profile.reply_style}\n"
    instructions += f"\nStyle: {persona.style}\n"
    called = address(persona, preferences, user_name)
    if user_name:
        instructions += f"\nYou are talking with {user_name}."
        if called and called != user_name:
            instructions += f' Address them as "{called}".\n'
        else:
            instructions += " Use their name naturally.\n"
    elif called:
        instructions += f'\nAddress the user as "{called}".\n'
    if lines := preference_lines(preferences):
        instructions += "\nThe user asked for this; it overrides the style above:\n"
        instructions += "".join(f"- {line}\n" for line in lines)
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
        persona: Persona = CASUAL,
    ) -> None:
        self._profile = profile
        self._user_name = user_name
        self._memory_context = memory_context
        self._persona = persona
        super().__init__(
            instructions=self._compose(memory.preferences if memory else None),
            tools=memory_tools(memory, self.apply_preferences) if memory else None,
        )
        self._memory = memory

    def _compose(self, preferences: Preferences | None) -> str:
        return build_instructions(
            self._profile, self._user_name, self._memory_context, self._persona, preferences
        )

    async def apply_preferences(self, preferences: Preferences) -> None:
        """Use new preferences for the rest of the call: instructions and speaking pace."""
        await self.update_instructions(self._compose(preferences))
        tts = self.session.tts
        update = getattr(tts, "update_options", None)
        if self._profile.pace_option and update is not None:
            update(**{self._profile.pace_option: effective_pace(self._persona, preferences)})

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        if self._memory is None or self._memory.private or not (text := new_message.text_content):
            return
        recalled = render_recall(await self._memory.recall(text))
        if recalled:
            turn_ctx.add_message(role="system", content=recalled)
