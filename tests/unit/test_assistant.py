from livekit.agents import llm

from baatein.agent.assistant import Assistant, build_instructions
from baatein.voice.languages import LanguageProfile, ProviderSpec
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
    assert "add_followup" in text
    assert "add_followup" not in build_instructions(PROFILE)


def test_assistant_without_memory_has_no_tools() -> None:
    assert Assistant(PROFILE).tools == []


async def test_assistant_with_memory_exposes_memory_tools() -> None:
    world = build_world()
    memory = await world.service.open_call(world.user)

    assistant = Assistant(PROFILE, memory=memory, memory_context="ctx")

    names = {tool.info.name for tool in assistant.tools if isinstance(tool, llm.FunctionTool)}
    assert names == {"recall_memory", "add_followup", "resolve_followup"}


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
