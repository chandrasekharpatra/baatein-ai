from pathlib import Path

import pytest

from baatein.config import Settings, UnsupportedDatabaseError


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


def test_llm_spec_omits_temperature_by_default() -> None:
    spec = make_settings().llm.spec()

    assert spec.provider == "anthropic"
    assert spec.options == {"model": "claude-haiku-5-5"}


def test_llm_spec_includes_temperature_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAATEIN_LLM__TEMPERATURE", "0.4")

    assert make_settings().llm.spec().options == {"model": "claude-haiku-5-5", "temperature": 0.4}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite:///data/baatein.db", Path("data/baatein.db")),
        ("sqlite:////var/lib/baatein.db", Path("/var/lib/baatein.db")),
    ],
)
def test_sqlite_path(url: str, expected: Path) -> None:
    assert make_settings(database={"url": url}).database.sqlite_path() == expected


@pytest.mark.parametrize("url", ["postgresql://localhost/baatein", "sqlite:///", "data.db"])
def test_unsupported_database_url(url: str) -> None:
    settings = make_settings(database={"url": url})

    with pytest.raises(UnsupportedDatabaseError, match="unsupported database url"):
        settings.database.sqlite_path()


def test_cut_offs_are_measured_per_model_and_language() -> None:
    from baatein.config import EmbeddingSettings

    llama = EmbeddingSettings()
    minilm = EmbeddingSettings(
        provider="fastembed", model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )

    assert (llama.cut_off("english"), llama.cut_off("hinglish"), llama.cut_off()) == (
        0.20,
        0.14,
        0.12,
    )
    assert llama.cut_off("odia") == 0.12  # not measured yet: the default
    assert minilm.cut_off("english") == minilm.cut_off() == 0.3
    assert EmbeddingSettings(model="someone/new").cut_off("english") == 0.3


def test_cut_offs_can_be_set_by_hand() -> None:
    from baatein.config import EmbeddingSettings

    tuned = EmbeddingSettings(min_similarity={"english": 0.25, "default": 0.1})

    assert (tuned.cut_off("english"), tuned.cut_off("hinglish"), tuned.cut_off()) == (
        0.25,
        0.1,
        0.1,
    )
