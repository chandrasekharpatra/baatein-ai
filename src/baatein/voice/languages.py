from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LanguageProfile:
    code: str
    stt: ProviderSpec
    tts: ProviderSpec
    reply_style: str


class UnknownLanguageError(LookupError):
    pass


class LanguageRegistry:
    def __init__(self, profiles: Iterable[LanguageProfile] = ()) -> None:
        self._profiles: dict[str, LanguageProfile] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: LanguageProfile) -> None:
        if profile.code in self._profiles:
            raise ValueError(f"language profile '{profile.code}' is already registered")
        self._profiles[profile.code] = profile

    def get(self, code: str) -> LanguageProfile:
        try:
            return self._profiles[code]
        except KeyError:
            raise UnknownLanguageError(
                f"unknown language '{code}', known: {', '.join(self._profiles)}"
            ) from None

    def codes(self) -> list[str]:
        return list(self._profiles)


HINGLISH = LanguageProfile(
    code="hinglish",
    stt=ProviderSpec("sarvam", {"model": "saaras:v4", "mode": "codemix", "language": "hi-IN"}),
    tts=ProviderSpec(
        "sarvam", {"model": "bulbul:v3", "target_language_code": "hi-IN", "speaker": "shubh"}
    ),
    reply_style=(
        "Speak in natural, casual Hinglish the way urban Indians talk: mix Hindi and English "
        "freely, written in Roman script (e.g. 'Aaj ka din kaisa raha?'). Match the user's "
        "balance of Hindi and English."
    ),
)

ENGLISH = LanguageProfile(
    code="english",
    stt=ProviderSpec("sarvam", {"model": "saaras:v4", "mode": "transcribe", "language": "en-IN"}),
    tts=ProviderSpec("cartesia", {"model": "sonic-3", "language": "en"}),
    reply_style="Speak in simple, friendly Indian English.",
)


def default_language_registry() -> LanguageRegistry:
    return LanguageRegistry([HINGLISH, ENGLISH])
