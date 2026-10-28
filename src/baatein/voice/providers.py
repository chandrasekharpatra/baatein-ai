from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from livekit.agents import llm, stt, tts
from livekit.plugins import anthropic, cartesia, sarvam

from baatein.voice.languages import LanguageProfile, LanguageRegistry, ProviderSpec


class UnknownProviderError(LookupError):
    pass


class ProviderRegistry[T]:
    kind = "provider"

    def __init__(self, factories: Mapping[str, Callable[..., T]] | None = None) -> None:
        self._factories: dict[str, Callable[..., T]] = dict(factories or {})

    def register(self, name: str, factory: Callable[..., T]) -> None:
        if name in self._factories:
            raise ValueError(f"{self.kind} provider '{name}' is already registered")
        self._factories[name] = factory

    def create(self, spec: ProviderSpec) -> T:
        try:
            factory = self._factories[spec.provider]
        except KeyError:
            raise UnknownProviderError(
                f"{self.kind} provider '{spec.provider}' is not registered, "
                f"known: {', '.join(self._factories)}"
            ) from None
        return factory(**spec.options)


class STTRegistry(ProviderRegistry[stt.STT[Any]]):
    kind = "stt"


class TTSRegistry(ProviderRegistry[tts.TTS[Any]]):
    kind = "tts"


class LLMRegistry(ProviderRegistry[llm.LLM[Any]]):
    kind = "llm"


def default_stt_registry() -> STTRegistry:
    return STTRegistry({"sarvam": sarvam.STT, "sarvam_realtime": sarvam.STTRealtime})


def default_tts_registry() -> TTSRegistry:
    return TTSRegistry({"sarvam": sarvam.TTS, "cartesia": cartesia.TTS})


def default_llm_registry() -> LLMRegistry:
    return LLMRegistry({"anthropic": anthropic.LLM})


@dataclass(frozen=True)
class VoicePipeline:
    profile: LanguageProfile
    stt: stt.STT[Any]
    llm: llm.LLM[Any]
    tts: tts.TTS[Any]


class VoicePipelineFactory:
    def __init__(
        self,
        languages: LanguageRegistry,
        stt: STTRegistry,
        tts: TTSRegistry,
        llm: LLMRegistry,
        llm_spec: ProviderSpec,
    ) -> None:
        self._languages = languages
        self._stt = stt
        self._tts = tts
        self._llm = llm
        self._llm_spec = llm_spec

    def build(self, language_code: str) -> VoicePipeline:
        profile = self._languages.get(language_code)
        return VoicePipeline(
            profile=profile,
            stt=self._stt.create(profile.stt),
            llm=self._llm.create(self._llm_spec),
            tts=self._tts.create(profile.tts),
        )
