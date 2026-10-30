from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from baatein.registry import ProviderSpec


class LLMSettings(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-haiku-5-5"
    temperature: float | None = None

    def spec(self) -> ProviderSpec:
        options: dict[str, object] = {"model": self.model}
        if self.temperature is not None:
            options["temperature"] = self.temperature
        return ProviderSpec(self.provider, options)


class VoiceOverride(BaseModel):
    """Another text-to-speech voice for one language, e.g. an expressive voice for a demo."""

    provider: str
    options: dict[str, object] = Field(default_factory=dict)
    pace_option: str | None = None
    """The option that sets speaking speed for this provider ("pace" for Sarvam, "speed" for
    Cartesia); None if it has none."""


class VoiceSettings(BaseModel):
    tts: dict[str, VoiceOverride] = Field(default_factory=dict)
    """Voices to use instead of a language profile's own, by language code. For example
    BAATEIN_VOICE__TTS='{"hinglish": {"provider": "sarvam", "options": {"model":
    "bulbul:v4-flash", "speaker": "shalini_enhi_companion", "target_language_code": "hi-IN"},
    "pace_option": "pace"}}'. Unset: the profile's measured default."""
    default_language: str = "hinglish"
    default_persona: str = "casual"
    stt_max_retry: int = 5
    stt_retry_interval: float = 2.0
    stt_timeout_seconds: float = 10.0
    stt_max_errors: int = 6
    """STT failures in a row (each about 10 s) before Baatein apologises and ends the call."""


MEASURED_CUT_OFFS: dict[str, dict[str | None, float]] = {
    # From docs/RETRIEVAL_BENCHMARK.md (2026-10-30). Keyed by the language of the user's words;
    # None is for anything else, including the recall tool's English keywords. A Hinglish
    # user's words reach recall in Devanagari (Sarvam codemix), so "hinglish" uses Hindi's.
    "llama-text-embed-v2": {None: 0.12, "english": 0.20, "hinglish": 0.14},
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": {None: 0.3},
}


MEASURED_DUPLICATES: dict[str, float] = {
    # Similarity between two saved facts at which the new one repeats or updates the old
    # (2026-10-30, Ram's account): rephrased duplicates and updates scored 0.83-0.98, different
    # but related facts at most 0.74. Models not measured here skip the check.
    "llama-text-embed-v2": 0.80,
}


class EmbeddingSettings(BaseModel):
    provider: str = "pinecone"
    model: str = "llama-text-embed-v2"
    dimensions: int = 2048
    cache_dir: str | None = "data/models"
    """Where FastEmbed downloads local models."""
    min_similarity: dict[str, float] = Field(default_factory=dict)
    """Cut-offs to use instead of the measured ones, by language ("default" for the rest)."""
    duplicate_similarity: float | None = None
    """Use instead of the measured value in `MEASURED_DUPLICATES`."""

    def spec(self) -> ProviderSpec:
        options: dict[str, object] = {"model_id": self.model, "dimensions": self.dimensions}
        if self.provider == "fastembed":
            options["cache_dir"] = self.cache_dir
        return ProviderSpec(self.provider, options)

    def cut_off(self, language: str | None = None) -> float:
        """The lowest similarity that counts as a match for words in `language`. Similarity
        scales differ by model (llama's answers score 0.14-0.30, MiniLM's 0.3+) and by
        language, so the cut-off is measured per model, never one number for all."""
        explicit = self.min_similarity.get(
            language or "default", self.min_similarity.get("default")
        )
        if explicit is not None:
            return explicit
        measured = MEASURED_CUT_OFFS.get(self.model, {})
        return measured.get(language, measured.get(None, 0.3))

    def duplicate_at(self) -> float | None:
        """How similar a new memory must be to a saved one to count as the same; None skips
        the check (no measured value for this model)."""
        if self.duplicate_similarity is not None:
            return self.duplicate_similarity
        return MEASURED_DUPLICATES.get(self.model)


class MemorySettings(BaseModel):
    context_facts: int = 10
    context_threads: int = 10
    context_declined: int = 10
    check_in_gap_days: int = 3
    check_in_max_offers: int = 3
    recall_limit: int = 5
    recall_timeout_seconds: float = 0.0
    """How long a reply waits for the per-turn search; one still running joins the next turn.
    0 suits the default hosted embedder (about 450 ms), which would miss any short wait. With
    a local FastEmbed model (5-35 ms) set 0.1, so its memories reach this reply."""
    thinking_after_seconds: float = 0.3
    """If the recall tool's search takes longer than this, Baatein says "Hmm…" meanwhile."""


class FinalizeSettings(BaseModel):
    """The summary written after each call (slice 5)."""

    model: str = "claude-haiku-5-5"
    effort: str = "low"
    timeout_seconds: float = 60.0
    max_attempts: int = 5
    retry_base_seconds: float = 60.0
    """The first retry waits this long, then each one twice as long, up to `retry_max_seconds`."""
    retry_max_seconds: float = 3600.0
    interval_seconds: float = 10.0
    """How often `baatein serve` looks for finished calls; 0 turns the background loop off."""
    batch_size: int = 20


class DevSettings(BaseModel):
    clock_file: str | None = None
    """For recording demos across days: memory, reminders and summaries take "now" from this
    file, set with `baatein demo-date`. Logins keep real time. Never set in production."""


AGENT_NAME = "baatein"


class AuthSettings(BaseModel):
    jwt_secret: SecretStr | None = None
    token_ttl_minutes: int = 7 * 24 * 60
    min_password_length: int = 8
    signup_enabled: bool = False
    """Anyone can make an account. Off: accounts come from admins' seed uploads and the CLI."""
    seed_password: SecretStr | None = None
    """Password for accounts `baatein seed` creates, instead of the files' own (which are
    public in the repo). Set it wherever seed files are loaded on a deploy."""
    admin_emails: Annotated[list[str], NoDecode] = []
    """Who may load, list and download test accounts, e.g.
    BAATEIN_AUTH__ADMIN_EMAILS=asha@example.com,ravi@example.com."""

    @field_validator("admin_emails", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        return value.split(",") if isinstance(value, str) else value

    @field_validator("admin_emails")
    @classmethod
    def _normalize(cls, emails: list[str]) -> list[str]:
        return [email.strip().lower() for email in emails if email.strip()]

    def is_admin(self, email: str) -> bool:
        return email.strip().lower() in self.admin_emails


class LiveKitSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIVEKIT_", env_file=".env", extra="ignore")

    url: str = "ws://localhost:7880"
    api_key: str | None = None
    api_secret: SecretStr | None = None
    agent_name: str = AGENT_NAME
    token_ttl_minutes: int = 15


class UnsupportedDatabaseError(ValueError):
    pass


class DatabaseSettings(BaseModel):
    url: str = "sqlite:///data/baatein.db"

    def sqlite_path(self) -> Path:
        prefix = "sqlite:///"
        if not self.url.startswith(prefix) or self.url == prefix:
            raise UnsupportedDatabaseError(
                f"unsupported database url '{self.url}', expected sqlite:///<path>"
            )
        return Path(self.url.removeprefix(prefix))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BAATEIN_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    finalize: FinalizeSettings = Field(default_factory=FinalizeSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    livekit: LiveKitSettings = Field(default_factory=LiveKitSettings)
    dev: DevSettings = Field(default_factory=DevSettings)
