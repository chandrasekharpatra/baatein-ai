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


def test_odia_speaks_through_sarvam_and_kannada_through_cartesia() -> None:
    from baatein.voice.languages import KANNADA, ODIA

    registry = default_language_registry()
    assert registry.get("odia") is ODIA
    assert registry.get("kannada") is KANNADA
    # Cartesia rejects Odia on every model (checked 2026-10-30), so Odia uses Sarvam's pace.
    assert (ODIA.tts.provider, ODIA.tts.options["target_language_code"]) == ("sarvam", "od-IN")
    assert ODIA.pace_option == "pace"
    assert (KANNADA.tts.provider, KANNADA.tts.options["language"]) == ("cartesia", "kn")
    for profile in (ODIA, KANNADA):
        assert profile.stt.options["mode"] == "codemix"
        assert "never reply in Hindi" in profile.reply_style
        assert profile.formal_you
    assert ODIA.honorific is None  # its respectful suffixes depend on gender
    assert KANNADA.honorific == "avare"
