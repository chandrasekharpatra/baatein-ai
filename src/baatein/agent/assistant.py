import asyncio
import logging

from livekit.agents import Agent, llm

from baatein.agent.tools import memory_tools
from baatein.domain.models import Preferences, ScoredFact
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

logger = logging.getLogger(__name__)

BASE_INSTRUCTIONS = """\
You are Baatein, a warm and curious voice companion who remembers people and \
keeps conversations moving.

Your replies are spoken aloud, so:
- Keep each reply to one to three short sentences.
- Never use markdown, lists, emojis, or any formatting.
- End with at most one question, never two, and often none. Build on what the user just said.
"""

MEMORY_INSTRUCTIONS = """\
You remember this user across calls, but only what they agree to. (Quoted phrases below are \
Hindi examples; say them in the user's language.)
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
- Your context holds only some of what they asked you to remember. When they ask about \
something from before ("yaad hai…?", "meri shaadi kab hui thi?") that isn't in your context, \
call recall first with a few English keywords (e.g. "wedding Sabitri"). Only if recall finds \
nothing, say you don't have it saved. Never invent memories. Don't take back something you \
said earlier in the call unless they say it's wrong; if you're unsure it was saved, call \
recall to check first.
- Health, grief and private matters: only bring them up if the user does.
- When they mention a plan, a wish or something coming up, you may offer to ask about it \
next time; if they agree, call track_thread. When they tell you how a storyline went, call \
update_thread. Not doing it or changing their mind is fine; never make them feel guilty.
- If your context has a check-in, ask about it once, gently, within your first two or three \
replies. Once asked, it's done for this call: if they answer something else, say "nahi" or \
ignore it, don't ask again or rephrase it; only talk about it if they bring it up. Never \
raise anything else from memory on your own.
- If they ask you not to bring something up ("aaj painting yaad nahi dilana"), call \
set_boundary; this deletes nothing. If they reopen it, call lift_boundary.
- If they're unhappy with a suggestion you made, apologise and call note_declined_suggestion.
- If others can hear them ("kamre mein mehmaan hain"), call privacy_mode and keep the rest of \
the call free of personal details.
- If they ask you to remind them of something, read back the date and repeat (and a time \
only if they gave one) in simple words, and after "haan" call create_reminder. Never guess a \
date. You can't alert them at a time or on a day: you can only bring it up when they talk to \
you. So never promise a day or time ("9 baje yaad dila dungi", "11 November ko yaad \
dilaungi"); say "Jab bhi hum us waqt ke aas-paas baat karenge, main yaad dila dungi." To \
change one, use update_reminder, not a new reminder.
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
    instructions += f"\nStyle: {persona.style}"
    if persona.respectful and profile.formal_you:
        instructions += f" {profile.formal_you}"
    instructions += "\n"
    called = address(persona, preferences, user_name, profile.honorific)
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
            tools=memory_tools(memory, self.apply_preferences, profile.thinking)
            if memory
            else None,
        )
        self._memory = memory
        self._recalled: set[str] = set()
        self._check_in_noted = False
        self._late_notes: set[asyncio.Task[None]] = set()

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
        if self._memory is None:
            return
        await self._note_check_in_asked(turn_ctx)
        text = new_message.text_content
        if self._memory.private or not text:
            return
        # Started here, not on the final transcript: that came only ~25 ms earlier in live
        # calls, and each extra transcript segment cost another Pinecone call.
        search = self._memory.start_recall(text)
        found = await self._memory.recall(search)
        if found is None:  # still searching: what it finds joins the next turn
            search.add_done_callback(self._arrived_late)
        elif recalled := self._fresh(found):
            await self._add_note(turn_ctx, recalled)

    def _fresh(self, found: list[ScoredFact], late: bool = False) -> str | None:
        """The note for memories not yet shown in this call, which are then marked as shown."""
        new = [s for s in found if s.fact.id not in self._recalled]
        self._recalled.update(s.fact.id for s in new)
        return render_recall(new, late)

    def _arrived_late(self, search: "asyncio.Task[list[ScoredFact]]") -> None:
        """Keep memories from a search that finished after the reply started, for the next
        turn. With a hosted embedder that's most searches: waiting for them would add about
        450 ms to every reply, while a memory needed right away comes from the recall tool."""
        memory = self._memory
        if search.cancelled() or search.exception() is not None or memory is None:
            return
        if not memory.private and (note := self._fresh(search.result(), late=True)):
            task = asyncio.ensure_future(self._keep_note(note))
            self._late_notes.add(task)
            task.add_done_callback(self._late_notes.discard)

    async def _note_check_in_asked(self, turn_ctx: llm.ChatContext) -> None:
        """Once a reply has brought up the check-in, say so: told only "ask once", Claude
        sometimes asked again when the user answered something else (found in scenario #1)."""
        memory = self._memory
        if self._check_in_noted or memory is None or memory.check_in is None:
            return
        replies = (m.text_content or "" for m in self.chat_ctx.messages() if m.role == "assistant")
        if not any(memory.mentions_check_in(reply) for reply in replies):
            return
        self._check_in_noted = True
        await self._add_note(
            turn_ctx,
            f'You have already asked about "{memory.check_in.title}" in this call. Don\'t ask '
            "about it again; talk about it only if they bring it up.",
        )

    async def _add_note(self, turn_ctx: llm.ChatContext, note: str) -> None:
        """Add a hidden note for this reply and keep it in the call history. LiveKit drops
        turn_ctx changes after the reply; without the history copy, Claude later saw its own
        answer without the memory behind it and apologised for "inventing" it."""
        turn_ctx.add_message(role="system", content=note)
        await self._keep_note(note)

    async def _keep_note(self, note: str) -> None:
        history = self.chat_ctx.copy()
        history.add_message(role="system", content=note)
        try:
            await self.update_chat_ctx(history)
        except Exception:  # e.g. the call ended while a late search finished
            logger.warning("couldn't add a memory note to the call history", exc_info=True)
