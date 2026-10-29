from types import SimpleNamespace
from typing import Any

from livekit.agents import llm

from baatein.agent.assistant import Assistant, build_instructions
from baatein.domain.models import Pace, Preferences
from baatein.voice.languages import LanguageProfile, ProviderSpec
from baatein.voice.personas import ELDER
from tests.factories import make_fact
from tests.fakes.world import build_world

PROFILE = LanguageProfile("hinglish", ProviderSpec("s"), ProviderSpec("t"), "Speak Hinglish.")


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
    assert "bring it up once" in text
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

    assert ELDER.style in text
    assert 'You are talking with Mohan. Address them as "Mohan ji".' in text
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
