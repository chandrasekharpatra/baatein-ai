"""Keep a call alive through short speech-to-text outages, and never let it end in silence.

On 2026-10-30 Sarvam's STT websocket stopped accepting connections for about a minute. With
LiveKit's defaults the session closed after ~20 s without a word, so the user heard the
greeting and then nothing. Here STT failures are retried for longer, and if they still don't
stop, Baatein says it can't hear the user (TTS still works) before ending the call.
"""

import logging

from livekit.agents import AgentSession, APIConnectOptions
from livekit.agents.voice import ErrorEvent, UserInputTranscribedEvent
from livekit.agents.voice.agent_session import SessionConnectOptions

from baatein.config import VoiceSettings

logger = logging.getLogger(__name__)


def connect_options(settings: VoiceSettings) -> SessionConnectOptions:
    """Longer STT retries; LiveKit's own error limit sits above ours so `SpeechGuard` decides."""
    return SessionConnectOptions(
        stt_conn_options=APIConnectOptions(
            max_retry=settings.stt_max_retry,
            retry_interval=settings.stt_retry_interval,
            timeout=settings.stt_timeout_seconds,
        ),
        max_unrecoverable_errors=settings.stt_max_errors + 1,
    )


class SpeechGuard:
    """Counts STT failures in a row; past the limit, apologises and ends the call itself."""

    def __init__(self, session: AgentSession[None], max_errors: int, message: str) -> None:
        self._session = session
        self._max_errors = max_errors
        self._message = message
        self._errors = 0
        self.ended = False
        session.on("error", self._on_error)
        session.on("user_input_transcribed", self._on_transcript)

    def _on_transcript(self, _event: UserInputTranscribedEvent) -> None:
        self._errors = 0

    def _on_error(self, event: ErrorEvent) -> None:
        error = event.error
        if getattr(error, "type", None) != "stt_error" or error.recoverable or self.ended:
            return
        self._errors += 1
        if self._errors <= self._max_errors:
            logger.warning(
                "speech-to-text failed (%d of %d), retrying", self._errors, self._max_errors
            )
            return
        logger.error("speech-to-text kept failing; telling the user and ending the call")
        self.ended = True
        error.recoverable = True  # stop LiveKit closing silently; we close after speaking
        self._session.say(self._message, allow_interruptions=False)
        self._session.shutdown(drain=True)
