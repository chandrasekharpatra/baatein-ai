import pytest

from baatein.config import Settings


def make_settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def test_defaults() -> None:
    settings = make_settings()

    assert settings.llm.provider == "anthropic"
    assert settings.llm.model == "claude-haiku-5-5"
    assert settings.voice.default_language == "hinglish"


def test_nested_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAATEIN_LLM__MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("BAATEIN_VOICE__DEFAULT_LANGUAGE", "english")

    settings = make_settings()

    assert settings.llm.model == "claude-sonnet-5-5"
    assert settings.voice.default_language == "english"


def test_llm_spec_carries_model_options() -> None:
    spec = make_settings().llm.spec()

    assert spec.provider == "anthropic"
    assert spec.options == {"model": "claude-haiku-5-5", "temperature": 0.7}
