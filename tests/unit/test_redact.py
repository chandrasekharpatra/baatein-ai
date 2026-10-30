import pytest

from baatein.memory.redact import REDACTED, redact


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("OTP hai 4821", f"OTP hai {REDACTED}"),
        ("mera pin 1234 hai", f"mera pin {REDACTED} hai"),
        ("UPI PIN: 9876", f"UPI PIN: {REDACTED}"),
        ("password is 5544", f"password is {REDACTED}"),
        ("ओटीपी 5521 bataya", f"ओटीपी {REDACTED} bataya"),
        ("card 4111 1111 1111 1111", f"card {REDACTED}"),
        ("account 123456789012", f"account {REDACTED}"),
        ("call me 98765 43210", f"call me {REDACTED}"),
    ],
)
def test_codes_and_long_numbers_are_removed(text: str, expected: str) -> None:
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "birthday 18 November 2026",
        "interview at 10:30, 2 baje",
        "Rs 500 diye",
        "2026-11-18",
        "pinky ne 1234 bola",
        "version 1.2.3456789",
    ],
)
def test_dates_times_amounts_and_ordinary_words_are_kept(text: str) -> None:
    assert redact(text) == text
