from pathlib import Path

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

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


class VoiceSettings(BaseModel):
    default_language: str = "hinglish"


class EmbeddingSettings(BaseModel):
    provider: str = "fastembed"
    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    dimensions: int = 384
    cache_dir: str | None = "data/models"

    def spec(self) -> ProviderSpec:
        return ProviderSpec(
            self.provider,
            {"model_id": self.model, "dimensions": self.dimensions, "cache_dir": self.cache_dir},
        )


class MemorySettings(BaseModel):
    min_similarity: float = 0.3
    context_summaries: int = 3
    context_followups: int = 5
    context_facts: int = 10
    recall_limit: int = 5
    recall_timeout_seconds: float = 0.3


AGENT_NAME = "baatein"


class AuthSettings(BaseModel):
    jwt_secret: SecretStr | None = None
    token_ttl_minutes: int = 7 * 24 * 60
    min_password_length: int = 8


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
    auth: AuthSettings = Field(default_factory=AuthSettings)
    livekit: LiveKitSettings = Field(default_factory=LiveKitSettings)
