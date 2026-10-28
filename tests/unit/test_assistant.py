from baatein.agent.assistant import Assistant, build_instructions
from baatein.voice.languages import LanguageProfile, ProviderSpec

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
