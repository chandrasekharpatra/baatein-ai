from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from baatein.registry import ProviderSpec


class LLMSettings(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-haiku-5-5"
    temperature: float = 0.7

    def spec(self) -> ProviderSpec:
        return ProviderSpec(self.provider, {"model": self.model, "temperature": self.temperature})


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
