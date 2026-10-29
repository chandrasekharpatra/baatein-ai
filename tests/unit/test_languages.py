import pytest

from baatein.voice.languages import (
    LanguageProfile,
    LanguageRegistry,
    ProviderSpec,
    UnknownLanguageError,
    default_language_registry,
)


def profile(code: str) -> LanguageProfile:
    return LanguageProfile(
        code=code,
        stt=ProviderSpec("fake-stt"),
        tts=ProviderSpec("fake-tts"),
        reply_style=f"reply in {code}",
    )


def test_get_returns_registered_profile() -> None:
    registry = LanguageRegistry([profile("hinglish")])

    assert registry.get("hinglish").reply_style == "reply in hinglish"


def test_get_unknown_language_raises() -> None:
    registry = LanguageRegistry([profile("hinglish")])

    with pytest.raises(UnknownLanguageError, match="tamil"):
        registry.get("tamil")


def test_register_rejects_duplicate_code() -> None:
    registry = LanguageRegistry([profile("hinglish")])

    with pytest.raises(ValueError, match="hinglish"):
        registry.register(profile("hinglish"))


def test_codes_lists_registered_profiles_in_order() -> None:
    registry = LanguageRegistry([profile("hinglish"), profile("english")])

    assert registry.codes() == ["hinglish", "english"]


def test_default_registry_includes_hinglish_with_codemix_stt() -> None:
    hinglish = default_language_registry().get("hinglish")

    assert hinglish.stt.provider == "sarvam"
    assert hinglish.stt.options["mode"] == "codemix"
    assert "Hinglish" in hinglish.reply_style


def test_hinglish_speaks_through_cartesia_in_hindi_with_speed_control() -> None:
    hinglish = default_language_registry().get("hinglish")

    assert (hinglish.tts.provider, hinglish.tts.options["language"]) == ("cartesia", "hi")
    assert hinglish.pace_option == "speed"
    assert hinglish.cannot_hear.startswith("Maaf kijiye")
    assert default_language_registry().get("english").pace_option == "speed"
