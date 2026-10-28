from injector import Binder, Injector, Module, provider, singleton

from baatein.config import Settings
from baatein.ports.clock import Clock, SystemClock
from baatein.ports.repos import FollowUpRepo, SessionRepo, TurnRepo, UserRepo
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.repos import (
    SqliteFollowUpRepo,
    SqliteSessionRepo,
    SqliteTurnRepo,
    SqliteUserRepo,
)
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
        binder.bind(Clock, to=SystemClock, scope=singleton)  # type: ignore[type-abstract]


class StorageModule(Module):
    @singleton
    @provider
    def database(self, settings: Settings) -> Database:
        return Database(settings.database.sqlite_path())

    @singleton
    @provider
    def users(self, db: Database) -> UserRepo:
        return SqliteUserRepo(db)

    @singleton
    @provider
    def sessions(self, db: Database) -> SessionRepo:
        return SqliteSessionRepo(db)

    @singleton
    @provider
    def turns(self, db: Database) -> TurnRepo:
        return SqliteTurnRepo(db)

    @singleton
    @provider
    def followups(self, db: Database) -> FollowUpRepo:
        return SqliteFollowUpRepo(db)


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
    return Injector([CoreModule(settings), StorageModule(), VoiceModule(), *overrides])
