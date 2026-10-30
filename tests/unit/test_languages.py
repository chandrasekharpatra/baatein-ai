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


def test_speaks_through_sarvam_bulbul_v4_flash_with_pace_control() -> None:
    registry = default_language_registry()
    voices = {
        code: (
            registry.get(code).tts.provider,
            registry.get(code).tts.options["model"],
            registry.get(code).tts.options["target_language_code"],
            registry.get(code).pace_option,
        )
        for code in ("hinglish", "english", "kannada")
    }

    assert voices == {
        "hinglish": ("sarvam", "bulbul:v4-flash", "hi-IN", "pace"),
        "english": ("sarvam", "bulbul:v4-flash", "en-IN", "pace"),
        "kannada": ("sarvam", "bulbul:v4-flash", "kn-IN", "pace"),
    }
    assert registry.get("hinglish").tts.options["speaker"] == "ishita_enhi_companion"
    assert registry.get("hinglish").cannot_hear.startswith("Maaf kijiye")


def test_odia_and_kannada_have_their_own_script_and_respect() -> None:
    from baatein.voice.languages import KANNADA, ODIA

    registry = default_language_registry()
    assert registry.get("odia") is ODIA
    assert registry.get("kannada") is KANNADA
    # bulbul:v4-flash has no Odia voice yet (and Cartesia has no Odia), so Odia keeps v3.
    assert (ODIA.tts.options["model"], ODIA.tts.options["target_language_code"]) == (
        "bulbul:v3",
        "od-IN",
    )
    assert ODIA.pace_option == "pace"
    for profile in (ODIA, KANNADA):
        assert profile.stt.options["mode"] == "codemix"
        assert "never reply in Hindi" in profile.reply_style
        assert profile.formal_you
    assert ODIA.honorific is None  # its respectful suffixes depend on gender
    assert KANNADA.honorific == "avare"
