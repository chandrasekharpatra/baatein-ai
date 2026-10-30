from collections.abc import Iterable
from dataclasses import dataclass

from baatein.registry import ProviderSpec


@dataclass(frozen=True)
class LanguageProfile:
    code: str
    stt: ProviderSpec
    tts: ProviderSpec
    reply_style: str
    pace_option: str | None = None
    """The TTS option that sets speaking speed, if the provider has one."""
    cannot_hear: str = "Sorry, I can't hear you right now. Let's talk again in a little while."
    """Said before ending a call when speech-to-text stops working."""
    honorific: str | None = None
    """Respectful word after a name for a respectful persona, e.g. "ji" (Hindi)."""
    formal_you: str | None = None
    """How a respectful persona says "you" in this language."""
    thinking: str = "Hmm…"
    """Said while a slow memory lookup runs, so the caller hears a pause, not silence."""


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
    # Cartesia "Sneha - Empathetic Voice" (Hindi); it reads Roman-script Hinglish. Cartesia has
    # no word timestamps for Hindi, so LiveKit times the live transcript itself.
    tts=ProviderSpec(
        "cartesia",
        {
            "model": "sonic-3",
            "language": "hi",
            "voice": "6b02ffe5-e3cb-48c0-a023-c72f85953375",
            "word_timestamps": False,
        },
    ),
    reply_style=(
        "Speak in natural, casual Hinglish the way urban Indians talk: mix Hindi and English "
        "freely, written in Roman script (e.g. 'Aaj ka din kaisa raha?'). Match the user's "
        "balance of Hindi and English. Always write in Roman script, never Devanagari, even "
        "when the user's words reach you in Devanagari."
    ),
    pace_option="speed",
    cannot_hear=(
        "Maaf kijiye, aapki awaaz mujh tak nahi pahunch rahi. Thodi der mein phir baat karte hain."
    ),
    honorific="ji",
    formal_you="Say 'aap', never 'tum'.",
)

ENGLISH = LanguageProfile(
    code="english",
    stt=ProviderSpec("sarvam", {"model": "saaras:v4", "mode": "transcribe", "language": "en-IN"}),
    tts=ProviderSpec("cartesia", {"model": "sonic-3", "language": "en"}),
    reply_style="Speak in simple, friendly Indian English.",
    pace_option="speed",
    honorific="ji",
)

ODIA = LanguageProfile(
    code="odia",
    stt=ProviderSpec("sarvam", {"model": "saaras:v4", "mode": "codemix", "language": "od-IN"}),
    # Cartesia has no Odia (every model rejects "or"), so Odia speaks through Sarvam.
    tts=ProviderSpec(
        "sarvam", {"model": "bulbul:v3", "target_language_code": "od-IN", "speaker": "kavya"}
    ),
    reply_style=(
        "Speak in natural, everyday Odia, written in Odia script (e.g. 'ଆଜି ଦିନ କେମିତି "
        "ଗଲା?'). Keep the English words the user uses themselves (phone, doctor, TV) in Latin "
        "script. Never write Odia in Roman script, and never reply in Hindi."
    ),
    pace_option="pace",
    cannot_hear=("କ୍ଷମା କରିବେ, ଆପଣଙ୍କ କଥା ମୋ ପାଖରେ ପହଞ୍ଚୁନାହିଁ। ଟିକିଏ ପରେ ପୁଣି କଥା ହେବା।"),
    # Odia's respectful suffixes depend on gender (babu, apa), which Baatein doesn't know.
    formal_you="Say 'ଆପଣ' (apana), never 'ତୁମେ' or 'ତୁ'.",
)

KANNADA = LanguageProfile(
    code="kannada",
    stt=ProviderSpec("sarvam", {"model": "saaras:v4", "mode": "codemix", "language": "kn-IN"}),
    # Cartesia "Disha - Friendly Anchor" (Kannada); like Hindi, no word timestamps.
    tts=ProviderSpec(
        "cartesia",
        {
            "model": "sonic-3",
            "language": "kn",
            "voice": "4c00ff98-199c-4967-99ae-7b69125edcff",
            "word_timestamps": False,
        },
    ),
    reply_style=(
        "Speak in natural, everyday Kannada, written in Kannada script (e.g. 'ಇವತ್ತು ದಿನ "
        "ಹೇಗಿತ್ತು?'). Keep the English words the user uses themselves (phone, doctor, TV) in "
        "Latin script. Never write Kannada in Roman script, and never reply in Hindi."
    ),
    pace_option="speed",
    cannot_hear=("ಕ್ಷಮಿಸಿ, ನಿಮ್ಮ ಧ್ವನಿ ನನಗೆ ಕೇಳುತ್ತಿಲ್ಲ. ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಮಾತನಾಡೋಣ."),
    honorific="avare",
    formal_you="Say 'ನೀವು' (neevu), never 'ನೀನು'.",
)


def default_language_registry() -> LanguageRegistry:
    return LanguageRegistry([HINGLISH, ENGLISH, ODIA, KANNADA])
