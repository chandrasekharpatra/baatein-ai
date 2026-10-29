from dataclasses import dataclass, replace
from typing import Any

from livekit.agents import llm, stt, tts
from livekit.plugins import anthropic, cartesia, sarvam

from baatein.registry import ProviderRegistry, ProviderSpec
from baatein.voice.languages import LanguageProfile, LanguageRegistry


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


def anthropic_llm(**options: Any) -> anthropic.LLM:
    """Claude with plain tool schemas.

    Strict schemas allow at most 16 optional parameters across all tools, and the memory tools
    need more. The tools validate their own arguments (see `agent.tools.choice`).
    """
    return anthropic.LLM(_strict_tool_schema=False, **options)


def default_llm_registry() -> LLMRegistry:
    return LLMRegistry({"anthropic": anthropic_llm})


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

    def build(self, language_code: str, pace: float = 1.0) -> VoicePipeline:
        profile = self._languages.get(language_code)
        tts_spec = profile.tts
        if profile.pace_option and pace != 1.0:
            tts_spec = replace(tts_spec, options={**tts_spec.options, profile.pace_option: pace})
        return VoicePipeline(
            profile=profile,
            stt=self._stt.create(profile.stt),
            llm=self._llm.create(self._llm_spec),
            tts=self._tts.create(tts_spec),
        )
