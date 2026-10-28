from injector import Binder, Injector, Module, provider, singleton

from baatein.config import Settings
from baatein.voice.languages import LanguageRegistry, default_language_registry
from baatein.voice.providers import (
    LLMRegistry,
    STTRegistry,
    TTSRegistry,
    VoicePipelineFactory,
    default_llm_registry,
    default_stt_registry,
    default_tts_registry,
)


class CoreModule(Module):
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def configure(self, binder: Binder) -> None:
        binder.bind(Settings, to=self._settings or Settings(), scope=singleton)


class VoiceModule(Module):
    @singleton
    @provider
    def languages(self) -> LanguageRegistry:
        return default_language_registry()

    @singleton
    @provider
    def stt_registry(self) -> STTRegistry:
        return default_stt_registry()

    @singleton
    @provider
    def tts_registry(self) -> TTSRegistry:
        return default_tts_registry()

    @singleton
    @provider
    def llm_registry(self) -> LLMRegistry:
        return default_llm_registry()

    @singleton
    @provider
    def pipeline_factory(
        self,
        settings: Settings,
        languages: LanguageRegistry,
        stt: STTRegistry,
        tts: TTSRegistry,
        llm: LLMRegistry,
    ) -> VoicePipelineFactory:
        return VoicePipelineFactory(languages, stt, tts, llm, settings.llm.spec())


def build_root(*overrides: Module, settings: Settings | None = None) -> Injector:
    return Injector([CoreModule(settings), VoiceModule(), *overrides])
