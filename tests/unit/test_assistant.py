from types import SimpleNamespace
from typing import Any

from livekit.agents import llm

from baatein.agent.assistant import Assistant, build_instructions
from baatein.domain.models import Pace, Person, Preferences
from baatein.memory.service import CallMemory
from baatein.voice.languages import LanguageProfile, ProviderSpec
from baatein.voice.personas import ELDER
from tests.factories import at, make_fact, make_session, make_thread
from tests.fakes.world import build_world

PROFILE = LanguageProfile(
    "hinglish",
    ProviderSpec("s"),
    ProviderSpec("t"),
    "Speak Hinglish.",
    honorific="ji",
    formal_you="Say 'aap', never 'tum'.",
)


def test_instructions_include_language_reply_style() -> None:
    assert "Speak Hinglish." in build_instructions(PROFILE)


def test_instructions_forbid_markup_for_voice_output() -> None:
    assert "markdown" in build_instructions(PROFILE).lower()


def test_assistant_uses_built_instructions() -> None:
    assert Assistant(PROFILE).instructions == build_instructions(PROFILE)


def test_instructions_mention_user_name_when_known() -> None:
    assert "You are talking with Asha." in build_instructions(PROFILE, "Asha")
    assert "talking with" not in build_instructions(PROFILE)
    assert "Asha" in Assistant(PROFILE, user_name="Asha").instructions


def test_instructions_include_memory_context_and_guidance() -> None:
    text = build_instructions(PROFILE, memory_context="Today is Thu 1 Jan 2026.")

    assert "Today is Thu 1 Jan 2026." in text
    assert "Kya main yeh yaad rakhun?" in text
    assert "pause_saving" in text
    assert "set_boundary" in text
    assert "ask about it once" in text
    assert "pause_saving" not in build_instructions(PROFILE)


def test_assistant_without_memory_has_no_tools() -> None:
    assert Assistant(PROFILE).tools == []


async def test_assistant_with_memory_exposes_memory_tools() -> None:
    world = build_world()
    memory = await world.service.open_call(world.user)

    assistant = Assistant(PROFILE, memory=memory, memory_context="ctx")

    names = {tool.info.name for tool in assistant.tools if isinstance(tool, llm.FunctionTool)}
    assert names == {
        "recall",
        "remember",
        "correct_memory",
        "forget",
        "pause_saving",
        "rename_person",
        "track_thread",
        "update_thread",
        "forget_thread",
        "set_boundary",
        "lift_boundary",
        "note_declined_suggestion",
        "create_reminder",
        "update_reminder",
        "complete_occurrence",
        "skip_occurrence",
        "cancel_reminder",
        "set_preference",
        "privacy_mode",
    }


async def test_user_turn_injects_recalled_facts() -> None:
    world = build_world()
    await world.store.add(make_fact(world.user.id, "Has an interview at Google on Friday"))
    memory = await world.service.open_call(world.user)
    turn_ctx = llm.ChatContext()
    message = llm.ChatMessage(role="user", content=["Google interview ki tayari chal rahi hai"])

    await Assistant(PROFILE, memory=memory, memory_context="").on_user_turn_completed(
        turn_ctx, message
    )

    [injected] = turn_ctx.items
    assert isinstance(injected, llm.ChatMessage)
    assert injected.role == "system"
    assert "Has an interview at Google on Friday" in (injected.text_content or "")


async def test_recalled_facts_stay_in_the_call_history_once() -> None:
    # Without this, the next turn showed Claude its own answer but not the memory behind it,
    # and it apologised for having made the answer up.
    world = build_world()
    await world.store.add(make_fact(world.user.id, "Has an interview at Google on Friday"))
    memory = await world.service.open_call(world.user)
    assistant = Assistant(PROFILE, memory=memory, memory_context="")

    for said in ["Google interview ki tayari", "Google interview kal hai"]:
        await assistant.on_user_turn_completed(
            llm.ChatContext(), llm.ChatMessage(role="user", content=[said])
        )

    kept = [m.text_content or "" for m in assistant.chat_ctx.messages() if m.role == "system"]
    assert sum("Has an interview at Google on Friday" in text for text in kept) == 1


async def test_user_turn_adds_nothing_without_memory_matches_or_text() -> None:
    world = build_world()
    memory = await world.service.open_call(world.user)
    turn_ctx = llm.ChatContext()

    await Assistant(PROFILE, memory=memory).on_user_turn_completed(
        turn_ctx, llm.ChatMessage(role="user", content=["kuch bhi"])
    )
    await Assistant(PROFILE, memory=memory).on_user_turn_completed(
        turn_ctx, llm.ChatMessage(role="user", content=[])
    )
    await Assistant(PROFILE).on_user_turn_completed(
        turn_ctx, llm.ChatMessage(role="user", content=["hello"])
    )

    assert turn_ctx.items == []


async def test_user_turn_recalls_nothing_in_privacy_mode() -> None:
    world = build_world()
    await world.store.add(make_fact(world.user.id, "Has an interview at Google on Friday"))
    memory = await world.service.open_call(world.user)
    memory.enable_privacy_mode()
    turn_ctx = llm.ChatContext()

    await Assistant(PROFILE, memory=memory).on_user_turn_completed(
        turn_ctx, llm.ChatMessage(role="user", content=["Google interview"])
    )

    assert turn_ctx.items == []


def test_hinglish_replies_stay_in_roman_script() -> None:
    from baatein.voice.languages import HINGLISH

    assert "never Devanagari" in build_instructions(HINGLISH)


def test_instructions_follow_persona_address_and_preferences() -> None:
    text = build_instructions(
        PROFILE, "Mohan", persona=ELDER, preferences=Preferences(fewer_questions=True)
    )

    assert f"{ELDER.style} Say 'aap', never 'tum'." in text
    assert 'You are talking with Mohan. Address them as "Mohan ji".' in text
    assert "never 'tum'" not in build_instructions(PROFILE, "Mohan")  # casual persona
    assert "it overrides the style above:\n- They don't like many questions" in text
    assert "overrides" not in build_instructions(PROFILE, "Mohan")
    assert 'Address the user as "Bauji".' in build_instructions(
        PROFILE, preferences=Preferences(address_as="Bauji")
    )


class FakeTTS:
    def __init__(self) -> None:
        self.options: dict[str, Any] = {}

    def update_options(self, **options: Any) -> None:
        self.options.update(options)


async def apply(profile: LanguageProfile, tts: object, preferences: Preferences) -> Assistant:
    world = build_world()
    memory = await world.service.open_call(world.user)
    assistant = Assistant(profile, "Mohan", memory=memory, memory_context="ctx", persona=ELDER)
    assistant._activity = SimpleNamespace(session=SimpleNamespace(tts=tts))  # type: ignore[assignment]
    instructions: list[str] = []

    async def record(text: str) -> None:
        instructions.append(text)

    assistant.update_instructions = record  # type: ignore[method-assign]
    await assistant.apply_preferences(preferences)
    assistant.recorded = instructions  # type: ignore[attr-defined]
    return assistant


async def test_apply_preferences_updates_instructions_and_tts_pace() -> None:
    tts = FakeTTS()
    paced = LanguageProfile("h", ProviderSpec("s"), ProviderSpec("t"), "x", pace_option="pace")

    assistant = await apply(paced, tts, Preferences(pace=Pace.SLOW, fewer_questions=True))

    [text] = assistant.recorded  # type: ignore[attr-defined]
    assert "don't like many questions" in text
    assert "ctx" in text
    assert tts.options == {"pace": 0.85}


async def test_apply_preferences_skips_tts_without_a_pace_option() -> None:
    tts = FakeTTS()

    await apply(PROFILE, tts, Preferences(pace=Pace.SLOW))
    await apply(PROFILE, None, Preferences(pace=Pace.SLOW))

    assert tts.options == {}


async def call_with_check_in(about: str | None) -> tuple[Assistant, CallMemory]:
    """A call whose context picked the Ramesh storyline as its one check-in."""
    world = build_world()
    last = make_session(world.user.id, started_at=at(-3 * 24 * 60))
    world.state.sessions[last.id] = last
    if about:
        world.state.people["p1"] = Person("p1", world.user.id, about, at(0))
    thread = make_thread(
        world.user.id, "Visit the Puri temple", about_person_id="p1" if about else None
    )
    world.state.threads[thread.id] = thread
    memory = await world.service.open_call(world.user)
    await memory.context()
    return Assistant(PROFILE, memory=memory, memory_context=""), memory


async def say(assistant: Assistant, user: str, reply: str) -> None:
    await assistant.on_user_turn_completed(
        llm.ChatContext(), llm.ChatMessage(role="user", content=[user])
    )
    history = assistant.chat_ctx.copy()
    history.add_message(role="assistant", content=reply)
    await assistant.update_chat_ctx(history)


def notes(assistant: Assistant) -> list[str]:
    return [
        m.text_content or ""
        for m in assistant.chat_ctx.messages()
        if m.role == "system" and "already asked" in (m.text_content or "")
    ]


async def test_once_the_check_in_is_asked_later_replies_are_told_not_to_ask_again() -> None:
    assistant, memory = await call_with_check_in(about="Ramesh")
    assert memory.check_in is not None

    await say(assistant, "Namaste!", "Namaste! Aaj din kaisa raha?")
    assert notes(assistant) == []
    await say(assistant, "Theek hai.", "Achha. Ramesh ji se baat hui kya?")
    await say(assistant, "Main theek hoon, aap batao.", "Main bhi theek hoon.")
    await say(assistant, "Hmm.", "Aur kya chal raha hai?")

    assert notes(assistant) == [
        'You have already asked about "Visit the Puri temple" in this call. Don\'t ask about '
        "it again; talk about it only if they bring it up."
    ]


async def test_a_storyline_without_a_person_counts_as_asked_on_two_title_words() -> None:
    assistant, memory = await call_with_check_in(about=None)

    assert not memory.mentions_check_in("Aaj temple gaye the?")
    assert memory.mentions_check_in("Puri ke temple ka plan bana?")
    await say(assistant, "Namaste!", "Puri ke temple ka plan bana?")
    await say(assistant, "Pata nahi.", "Koi baat nahi.")
    assert len(notes(assistant)) == 1


async def test_no_check_in_means_no_note() -> None:
    world = build_world()
    memory = await world.service.open_call(world.user)
    await memory.context()
    assistant = Assistant(PROFILE, memory=memory, memory_context="")

    await say(assistant, "Namaste!", "Ramesh se baat hui?")
    await say(assistant, "Haan.", "Achha.")

    assert memory.check_in is None
    assert not memory.mentions_check_in("Ramesh se baat hui?")
    assert notes(assistant) == []
