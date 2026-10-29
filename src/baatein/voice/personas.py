"""How Baatein talks to a user: tone, form of address, pace, sentence length, questions.

A persona is chosen per user (decision 4); preferences the user states in a call fine-tune it.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from baatein.domain.models import Pace, Preferences, Verbosity

PACE_VALUES = {Pace.SLOW: 0.85, Pace.NORMAL: 1.0, Pace.FAST: 1.15}


@dataclass(frozen=True)
class Persona:
    code: str
    style: str
    """Instructions for tone, address, sentence length and question frequency."""
    greeting: str
    """Instructions for the first thing Baatein says in a call."""
    pace: Pace = Pace.NORMAL
    honorific: str | None = None
    """Added after the user's name, e.g. "ji" → "Mohan ji"."""


class UnknownPersonaError(LookupError):
    pass


class PersonaRegistry:
    def __init__(self, personas: Iterable[Persona] = ()) -> None:
        self._personas: dict[str, Persona] = {}
        for persona in personas:
            self.register(persona)

    def register(self, persona: Persona) -> None:
        if persona.code in self._personas:
            raise ValueError(f"persona '{persona.code}' is already registered")
        self._personas[persona.code] = persona

    def get(self, code: str) -> Persona:
        try:
            return self._personas[code]
        except KeyError:
            raise UnknownPersonaError(
                f"unknown persona '{code}', known: {', '.join(self._personas)}"
            ) from None

    def codes(self) -> list[str]:
        return list(self._personas)


ELDER = Persona(
    code="elder",
    style=(
        "The user is an older adult. Be respectful, patient and calm: say 'aap', never 'tum'. "
        "Speak Hindi first, with only the simple English words they use themselves. Use short, "
        "simple sentences, one idea at a time. Ask few questions; often just listen and reflect "
        "what they said. If they ask you to repeat, say it again more simply. You are an AI "
        "companion: warm, but never pretend to be a friend, relative or a replacement for the "
        "people in their life."
    ),
    greeting=(
        "Greet the user respectfully by name in one short sentence ('Namaste, Mohan ji!') and "
        "ask how they are."
    ),
    pace=Pace.SLOW,
    honorific="ji",
)

CASUAL = Persona(
    code="casual",
    style="Talk like a friendly, curious friend: relaxed, upbeat and easy-going.",
    greeting="Greet the user warmly in one short sentence and ask how their day is going.",
)


def default_persona_registry() -> PersonaRegistry:
    return PersonaRegistry([CASUAL, ELDER])


def effective_pace(persona: Persona, preferences: Preferences) -> float:
    """The TTS pace: the user's own choice, else the persona's."""
    return PACE_VALUES[preferences.pace or persona.pace]


def address(persona: Persona, preferences: Preferences, user_name: str | None) -> str | None:
    """How to address the user: what they asked for, else their name with the honorific."""
    if preferences.address_as:
        return preferences.address_as
    if user_name and persona.honorific:
        return f"{user_name} {persona.honorific}"
    return user_name


def preference_lines(preferences: Preferences) -> list[str]:
    """What the user asked for, as instructions that override the persona."""
    lines = []
    if preferences.pace is Pace.SLOW:
        lines.append("They find it hard to follow fast speech: use short, simple sentences.")
    if preferences.verbosity is Verbosity.SHORT:
        lines.append("Keep every reply to one or two short sentences.")
    elif preferences.verbosity is Verbosity.DETAILED:
        lines.append("They like fuller answers: you may use up to four sentences.")
    if preferences.fewer_questions:
        lines.append(
            "They don't like many questions: ask one only when really needed; usually ask none."
        )
    return lines
