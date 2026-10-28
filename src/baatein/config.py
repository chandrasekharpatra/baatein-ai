from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from baatein.voice.languages import ProviderSpec


class LLMSettings(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-haiku-5-5"
    temperature: float = 0.7

    def spec(self) -> ProviderSpec:
        return ProviderSpec(self.provider, {"model": self.model, "temperature": self.temperature})


class VoiceSettings(BaseModel):
    default_language: str = "hinglish"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BAATEIN_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
