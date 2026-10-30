import pytest

from baatein.domain.models import Pace, Preferences, Verbosity
from baatein.voice.personas import (
    CASUAL,
    ELDER,
    Persona,
    PersonaRegistry,
    UnknownPersonaError,
    address,
    default_persona_registry,
    effective_pace,
    preference_lines,
)


def test_registry_lists_gets_and_rejects() -> None:
    registry = default_persona_registry()

    assert registry.codes() == ["casual", "elder"]
    assert registry.get("elder") is ELDER
    with pytest.raises(UnknownPersonaError, match=r"pirate.*casual, elder"):
        registry.get("pirate")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(Persona("casual", "x", "y"))
    assert PersonaRegistry().codes() == []


def test_elder_speaks_slowly_unless_the_user_chose_a_pace() -> None:
    assert effective_pace(ELDER, Preferences()) == 0.85
    assert effective_pace(CASUAL, Preferences()) == 1.0
    assert effective_pace(ELDER, Preferences(pace=Pace.NORMAL)) == 1.0
    assert effective_pace(CASUAL, Preferences(pace=Pace.FAST)) == 1.15


def test_address_prefers_the_users_choice_then_the_honorific() -> None:
    assert address(ELDER, Preferences(), "Mohan", "ji") == "Mohan ji"
    assert address(ELDER, Preferences(), "Mohan", "avare") == "Mohan avare"
    assert address(ELDER, Preferences(), "Mohan") == "Mohan"  # Odia: no gender-free suffix
    assert address(CASUAL, Preferences(), "Mohan", "ji") == "Mohan"
    assert address(ELDER, Preferences(address_as="Mohan bhai"), "Mohan") == "Mohan bhai"
    assert address(ELDER, Preferences(), None) is None


def test_preference_lines() -> None:
    assert preference_lines(Preferences()) == []
    assert preference_lines(Preferences(pace=Pace.FAST, verbosity=Verbosity.NORMAL)) == []
    lines = preference_lines(
        Preferences(pace=Pace.SLOW, verbosity=Verbosity.SHORT, fewer_questions=True)
    )
    assert [line.split(":")[0].split(" ")[0] for line in lines] == ["They", "Keep", "They"]
    assert "one or two short sentences" in lines[1]
    assert "usually ask none" in lines[2]
    [detailed] = preference_lines(Preferences(verbosity=Verbosity.DETAILED))
    assert "up to four sentences" in detailed
