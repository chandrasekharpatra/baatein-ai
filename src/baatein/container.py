from datetime import timedelta
from pathlib import Path

from anthropic import AsyncAnthropic
from injector import Binder, Injector, Module, provider, singleton

from baatein.auth.livekit import LiveKitCallTokens
from baatein.auth.passwords import Argon2PasswordHasher
from baatein.auth.service import AuthService
from baatein.auth.tokens import JwtAccessTokens
from baatein.config import Settings
from baatein.domain.errors import ConfigurationError
from baatein.memory.analyzer import ClaudeAnalyzer
from baatein.memory.embeddings.registry import EmbedderRegistry, default_embedder_registry
from baatein.memory.finalizer import Finalizer
from baatein.memory.seed import Seeder
from baatein.memory.service import MemoryDeps, MemoryService
from baatein.ports.analyzer import CallAnalyzer
from baatein.ports.auth import AccessTokens, CallTokenIssuer, PasswordHasher
from baatein.ports.clock import Clock, DemoClock, SystemClock
from baatein.ports.embedder import Embedder
from baatein.ports.memory import MemoryStore
from baatein.ports.repos import (
    BoundaryRepo,
    PeopleRepo,
    ReminderRepo,
    SessionRepo,
    SuggestionRepo,
    ThreadRepo,
    TurnRepo,
    UserRepo,
)
from baatein.registry import ProviderSpec
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.memory_store import SqliteMemoryStore
from baatein.storage.sqlite.repos import (
    SqliteBoundaryRepo,
    SqlitePeopleRepo,
    SqliteReminderRepo,
    SqliteSessionRepo,
    SqliteSuggestionRepo,
    SqliteThreadRepo,
    SqliteTurnRepo,
    SqliteUserRepo,
)
from baatein.voice.languages import LanguageRegistry, default_language_registry
from baatein.voice.personas import PersonaRegistry, default_persona_registry
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
    def people(self, db: Database) -> PeopleRepo:
        return SqlitePeopleRepo(db)

    @singleton
    @provider
    def threads(self, db: Database) -> ThreadRepo:
        return SqliteThreadRepo(db)

    @singleton
    @provider
    def boundaries(self, db: Database) -> BoundaryRepo:
        return SqliteBoundaryRepo(db)

    @singleton
    @provider
    def suggestions(self, db: Database) -> SuggestionRepo:
        return SqliteSuggestionRepo(db)

    @singleton
    @provider
    def reminders(self, db: Database) -> ReminderRepo:
        return SqliteReminderRepo(db)


class MemoryModule(Module):
    @singleton
    @provider
    def embedder_registry(self) -> EmbedderRegistry:
        return default_embedder_registry()

    @singleton
    @provider
    def embedder(self, settings: Settings, registry: EmbedderRegistry) -> Embedder:
        return registry.create(settings.embedding.spec())

    @singleton
    @provider
    def sqlite_memory_store(
        self, db: Database, embedder: Embedder, settings: Settings
    ) -> SqliteMemoryStore:
        return SqliteMemoryStore(db, embedder, settings.embedding.cut_off())

    @singleton
    @provider
    def memory_store(self, store: SqliteMemoryStore) -> MemoryStore:
        return store

    @singleton
    @provider
    def memory_deps(
        self,
        settings: Settings,
        users: UserRepo,
        sessions: SessionRepo,
        turns: TurnRepo,
        people: PeopleRepo,
        threads: ThreadRepo,
        boundaries: BoundaryRepo,
        suggestions: SuggestionRepo,
        reminders: ReminderRepo,
        store: MemoryStore,
        clock: Clock,
    ) -> MemoryDeps:
        if settings.dev.clock_file:  # demos only; tokens and logins keep the real clock
            clock = DemoClock(Path(settings.dev.clock_file))
        return MemoryDeps(
            users,
            sessions,
            turns,
            people,
            threads,
            boundaries,
            suggestions,
            reminders,
            store,
            clock,
            settings.memory,
        )

    @singleton
    @provider
    def memory_service(
        self, deps: MemoryDeps, embedder: Embedder, settings: Settings
    ) -> MemoryService:
        return MemoryService(
            deps, embedder, settings.embedding.cut_off, settings.embedding.duplicate_at()
        )

    @singleton
    @provider
    def seeder(self, deps: MemoryDeps, personas: PersonaRegistry) -> Seeder:
        return Seeder(deps, personas)

    @singleton
    @provider
    def call_analyzer(self, settings: Settings) -> CallAnalyzer:
        return ClaudeAnalyzer(AsyncAnthropic(), settings.finalize.model, settings.finalize.effort)

    @singleton
    @provider
    def finalizer(self, deps: MemoryDeps, analyzer: CallAnalyzer, settings: Settings) -> Finalizer:
        return Finalizer(deps, analyzer, settings.finalize)


class AuthModule(Module):
    @singleton
    @provider
    def password_hasher(self) -> PasswordHasher:
        return Argon2PasswordHasher()

    @singleton
    @provider
    def access_tokens(self, settings: Settings, clock: Clock) -> AccessTokens:
        if settings.auth.jwt_secret is None:
            raise ConfigurationError("BAATEIN_AUTH__JWT_SECRET must be set to issue access tokens")
        return JwtAccessTokens(
            settings.auth.jwt_secret.get_secret_value(),
            timedelta(minutes=settings.auth.token_ttl_minutes),
            clock,
        )

    @singleton
    @provider
    def call_tokens(self, settings: Settings) -> CallTokenIssuer:
        livekit = settings.livekit
        if livekit.api_key is None or livekit.api_secret is None:
            raise ConfigurationError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set")
        return LiveKitCallTokens(
            livekit.url,
            livekit.api_key,
            livekit.api_secret.get_secret_value(),
            livekit.agent_name,
            timedelta(minutes=livekit.token_ttl_minutes),
        )

    @singleton
    @provider
    def auth_service(
        self,
        settings: Settings,
        users: UserRepo,
        hasher: PasswordHasher,
        tokens: AccessTokens,
        languages: LanguageRegistry,
        personas: PersonaRegistry,
        clock: Clock,
    ) -> AuthService:
        return AuthService(
            users,
            hasher,
            tokens,
            languages,
            personas,
            clock,
            default_language=settings.voice.default_language,
            default_persona=settings.voice.default_persona,
            min_password_length=settings.auth.min_password_length,
        )


class VoiceModule(Module):
    @singleton
    @provider
    def languages(self, settings: Settings) -> LanguageRegistry:
        return default_language_registry(
            {
                code: (ProviderSpec(voice.provider, voice.options), voice.pace_option)
                for code, voice in settings.voice.tts.items()
            }
        )

    @singleton
    @provider
    def personas(self) -> PersonaRegistry:
        return default_persona_registry()

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
    return Injector(
        [
            CoreModule(settings),
            StorageModule(),
            MemoryModule(),
            AuthModule(),
            VoiceModule(),
            *overrides,
        ]
    )
