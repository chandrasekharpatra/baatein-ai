from dataclasses import dataclass
from typing import Any

import pytest

from baatein.voice.languages import LanguageProfile, LanguageRegistry, ProviderSpec
from baatein.voice.providers import (
    LLMRegistry,
    ProviderRegistry,
    STTRegistry,
    TTSRegistry,
    UnknownProviderError,
    VoicePipelineFactory,
)


@dataclass
class Built:
    kind: str
    options: dict[str, Any]


def recorder(kind: str):  # type: ignore[no-untyped-def]
    return lambda **options: Built(kind, options)


def test_create_passes_spec_options_to_factory() -> None:
    registry: ProviderRegistry[Built] = STTRegistry({"fake": recorder("fake")})

    built = registry.create(ProviderSpec("fake", {"language": "hi-IN"}))

    assert built == Built("fake", {"language": "hi-IN"})


def test_create_unknown_provider_raises_with_kind_and_known_names() -> None:
    registry: ProviderRegistry[Built] = TTSRegistry({"fake": recorder("fake")})

    with pytest.raises(UnknownProviderError, match=r"tts provider 'nope'.*fake"):
        registry.create(ProviderSpec("nope"))


def test_register_adds_provider_and_rejects_duplicates() -> None:
    registry: ProviderRegistry[Built] = LLMRegistry()
    registry.register("fake", recorder("fake"))

    assert registry.create(ProviderSpec("fake")).kind == "fake"
    with pytest.raises(ValueError, match="fake"):
        registry.register("fake", recorder("other"))


def test_pipeline_factory_builds_components_for_language() -> None:
    languages = LanguageRegistry(
        [
            LanguageProfile(
                code="hinglish",
                stt=ProviderSpec("s", {"mode": "codemix"}),
                tts=ProviderSpec("t", {"voice": "v"}),
                reply_style="hinglish",
            )
        ]
    )
    factory = VoicePipelineFactory(
        languages=languages,
        stt=STTRegistry({"s": recorder("stt")}),
        tts=TTSRegistry({"t": recorder("tts")}),
        llm=LLMRegistry({"l": recorder("llm")}),
        llm_spec=ProviderSpec("l", {"model": "m"}),
    )

    pipeline = factory.build("hinglish")

    assert pipeline.profile.code == "hinglish"
    assert pipeline.stt == Built("stt", {"mode": "codemix"})
    assert pipeline.tts == Built("tts", {"voice": "v"})
    assert pipeline.llm == Built("llm", {"model": "m"})
