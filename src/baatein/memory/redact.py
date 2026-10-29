import re

REDACTED = "[redacted]"

# A code right after a word like OTP or PIN, in English or Devanagari ("OTP hai 4821").
_CODE_AFTER_KEYWORD = re.compile(
    r"(?P<key>\b(?:otp|pin|cvv|mpin|upi\s+pin|password|passcode)\b|ओटीपी|पिन|पासवर्ड)"
    r"(?P<gap>[\s:\-]*(?:is|hai|tha|था|है)?[\s:\-]*)"
    r"(?P<code>\d(?:\s?\d){2,7})",
    re.IGNORECASE,
)
# Six or more digits, optionally grouped by spaces: card, account, phone and similar numbers.
_LONG_NUMBER = re.compile(r"(?<![\w.])\d(?: ?\d){5,}(?![\w.])")


def redact(text: str) -> str:
    """Remove codes and long numbers (OTP, PIN, card, account, phone) before anything is stored."""
    text = _CODE_AFTER_KEYWORD.sub(lambda m: f"{m['key']}{m['gap']}{REDACTED}", text)
    return _LONG_NUMBER.sub(REDACTED, text)
