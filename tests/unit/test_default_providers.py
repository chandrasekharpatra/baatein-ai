import pytest
from livekit.agents import llm, stt, tts

from baatein.config import Settings
from baatein.voice.languages import default_language_registry
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

    assert isinstance(default_llm_registry().create(spec), llm.LLM)
