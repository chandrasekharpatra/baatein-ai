from types import SimpleNamespace
from typing import Any

import pytest

from baatein.agent.resilience import SpeechGuard, connect_options
from baatein.config import VoiceSettings

SORRY = "Maaf kijiye, aapki awaaz mujh tak nahi pahunch rahi."


class FakeSession:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.said: list[tuple[str, bool]] = []
        self.shutdowns: list[bool] = []

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    def say(self, text: str, *, allow_interruptions: bool) -> None:
        self.said.append((text, allow_interruptions))

    def shutdown(self, *, drain: bool) -> None:
        self.shutdowns.append(drain)


def stt_error(recoverable: bool = False, kind: str = "stt_error") -> Any:
    return SimpleNamespace(error=SimpleNamespace(type=kind, recoverable=recoverable))


def guard(max_errors: int = 2) -> tuple[FakeSession, SpeechGuard]:
    session = FakeSession()
    return session, SpeechGuard(session, max_errors, SORRY)  # type: ignore[arg-type]


def test_connect_options_retry_stt_longer_and_leave_closing_to_the_guard() -> None:
    options = connect_options(
        VoiceSettings(
            stt_max_retry=7, stt_retry_interval=3, stt_timeout_seconds=12, stt_max_errors=4
        )
    )

    stt = options.stt_conn_options
    assert (stt.max_retry, stt.retry_interval, stt.timeout) == (7, 3, 12)
    assert options.max_unrecoverable_errors == 5


def test_short_outages_are_ridden_out(caplog: pytest.LogCaptureFixture) -> None:
    session, speech = guard(max_errors=2)

    for _ in range(2):
        session.handlers["error"](stt_error())

    assert (session.said, session.shutdowns, speech.ended) == ([], [], False)
    assert "speech-to-text failed (2 of 2)" in caplog.text


def test_hearing_again_resets_the_count() -> None:
    session, speech = guard(max_errors=2)

    for _ in range(2):
        session.handlers["error"](stt_error())
    session.handlers["user_input_transcribed"](object())
    for _ in range(2):
        session.handlers["error"](stt_error())

    assert not speech.ended


def test_a_long_outage_ends_with_an_apology_not_silence() -> None:
    session, speech = guard(max_errors=2)
    events = [stt_error() for _ in range(4)]

    for event in events:
        session.handlers["error"](event)

    assert speech.ended
    assert session.said == [(SORRY, False)]
    assert session.shutdowns == [True]
    assert events[2].error.recoverable is True


def test_other_errors_and_recoverable_ones_are_left_to_livekit() -> None:
    session, speech = guard(max_errors=0)

    session.handlers["error"](stt_error(recoverable=True))
    session.handlers["error"](stt_error(kind="tts_error"))
    session.handlers["error"](SimpleNamespace(error=object()))

    assert not speech.ended
    assert session.said == []
