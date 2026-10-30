import pytest
from livekit.agents import llm, stt, tts

from baatein.config import Settings
from baatein.registry import ProviderSpec
from baatein.voice.languages import UnknownLanguageError, default_language_registry
from baatein.voice.providers import (
    default_llm_registry,
    default_stt_registry,
    default_tts_registry,
)


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("SARVAM_API_KEY", "CARTESIA_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test-key")


@pytest.mark.parametrize("code", default_language_registry().codes())
def test_default_profiles_build_real_plugins(code: str) -> None:
    profile = default_language_registry().get(code)

    assert isinstance(default_stt_registry().create(profile.stt), stt.STT)
    assert isinstance(default_tts_registry().create(profile.tts), tts.TTS)


def test_default_llm_spec_builds_real_plugin() -> None:
    spec = Settings(_env_file=None).llm.spec()  # type: ignore[call-arg]

    claude = default_llm_registry().create(spec)

    assert isinstance(claude, llm.LLM)
    assert claude._opts.strict_tool_schema is False  # type: ignore[attr-defined]
    assert claude._opts.caching == "ephemeral"  # type: ignore[attr-defined]


DEMO_VOICE = (
    '{"hinglish": {"provider": "sarvam", "pace_option": "pace", "options": {"model":'
    ' "bulbul:v4-flash", "speaker": "shalini_enhi_companion", "target_language_code": "hi-IN"}}}'
)


def test_a_voice_override_replaces_one_languages_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAATEIN_VOICE__TTS", DEMO_VOICE)
    voices = Settings(_env_file=None).voice.tts  # type: ignore[call-arg]

    registry = default_language_registry(
        {code: (ProviderSpec(v.provider, v.options), v.pace_option) for code, v in voices.items()}
    )

    hinglish = registry.get("hinglish")
    assert (hinglish.tts.provider, hinglish.pace_option) == ("sarvam", "pace")
    assert isinstance(default_tts_registry().create(hinglish.tts), tts.TTS)  # a real voice name
    assert registry.get("english") == default_language_registry().get("english")


def test_a_voice_override_for_an_unknown_language_is_refused() -> None:
    with pytest.raises(UnknownLanguageError, match="klingon"):
        default_language_registry({"klingon": (ProviderSpec("sarvam"), None)})
