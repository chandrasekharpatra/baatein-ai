"""Summarise each call after it ends, retrying failures with backoff (slice 5).

`baatein serve` runs `Finalizer.run_forever` in the background; `baatein finalize` runs it once.
A call is ready as soon as it ends: `CallMemory.close` writes its last turns before marking it
pending.
"""

import asyncio
import logging
from datetime import timedelta

from baatein.config import FinalizeSettings
from baatein.domain.errors import NotFoundError
from baatein.domain.models import CallSummary, Role, Sensitivity, Session
from baatein.memory.redact import redact
from baatein.memory.service import MemoryDeps
from baatein.ports.analyzer import CallAnalyzer, CallRecord

logger = logging.getLogger(__name__)

PAUSED_SUMMARY = "They asked not to save anything from this call."
MAX_SUMMARY_CHARS = 300
MAX_TOPIC_CHARS = 200
MAX_NOTES = 2
MAX_NOTE_CHARS = 200


def clean(result: CallSummary, user_spoke_last: bool = True) -> CallSummary:
    """Redact and trim what the analyzer wrote. A call only broke off mid-story if the user
    had the last word: hanging up while Baatein talks is a normal end."""
    summary = redact(result.summary or "").strip()[:MAX_SUMMARY_CHARS]
    topic = redact(result.unfinished_topic or "").strip()[:MAX_TOPIC_CHARS]
    notes = [redact(note).strip()[:MAX_NOTE_CHARS] for note in result.notes]
    abrupt = result.ended_abruptly and user_spoke_last
    return CallSummary(
        summary or None,
        ended_abruptly=abrupt,
        unfinished_topic=topic if abrupt and topic else None,
        notes=tuple(note for note in notes if note)[:MAX_NOTES],
    )


class Finalizer:
    def __init__(
        self, deps: MemoryDeps, analyzer: CallAnalyzer, settings: FinalizeSettings
    ) -> None:
        self._deps = deps
        self._analyzer = analyzer
        self._settings = settings

    async def run_forever(self) -> None:
        """Summarise finished calls every `interval_seconds`, until cancelled."""
        while True:
            try:
                await self.run_due()
            except Exception:
                logger.exception("finalizing calls failed")
            await asyncio.sleep(self._settings.interval_seconds)

    async def run_due(self) -> int:
        """Summarise the calls that are due now. Returns how many succeeded."""
        due = await self._deps.sessions.pending_finalization(
            self._settings.max_attempts, self._settings.batch_size, self._deps.clock.now()
        )
        done = 0
        for session in due:
            done += await self.finalize(session)
        return done

    async def finalize_by_id(self, session_id: str) -> bool:
        """Summarise one call again, whatever its state (for `baatein finalize --session`)."""
        session = await self._deps.sessions.get(session_id)
        if session is None:
            raise NotFoundError("session", session_id)
        return await self.finalize(session)

    async def finalize(self, session: Session) -> bool:
        try:
            result = await self._summarise(session)
        except Exception:
            attempt = session.finalize_attempts + 1
            delay = min(
                self._settings.retry_base_seconds * 2 ** (attempt - 1),
                self._settings.retry_max_seconds,
            )
            logger.exception(
                "summarising call %s failed (attempt %d of %d)",
                session.id,
                attempt,
                self._settings.max_attempts,
            )
            retry_at = self._deps.clock.now() + timedelta(seconds=delay)
            await self._deps.sessions.mark_finalize_failed(session.id, retry_at)
            return False
        await self._deps.sessions.mark_finalized(session.id, result)
        return True

    async def _summarise(self, session: Session) -> CallSummary:
        if session.saving_paused:
            return CallSummary(PAUSED_SUMMARY)
        turns = await self._deps.turns.list_for_session(session.id)
        if not any(turn.role is Role.USER for turn in turns):
            return CallSummary(None)
        user = await self._deps.users.get(session.user_id)
        if user is None:
            raise NotFoundError("user", session.user_id)
        boundaries = await self._deps.boundaries.list_active(
            user.id, session.id, self._deps.clock.now()
        )
        call = CallRecord(
            user_name=user.preferences.address_as or user.display_name or "the user",
            turns=turns,
            saved=await self._saved(session),
            avoid=[boundary.topic for boundary in boundaries],
        )
        result = await asyncio.wait_for(
            self._analyzer.analyze(call), self._settings.timeout_seconds
        )
        return clean(result, user_spoke_last=turns[-1].role is Role.USER)

    async def _saved(self, session: Session) -> list[str]:
        """What the user agreed to keep in this call. Sensitive items are left out, so the
        summary that opens the next call can't raise them (they're only raised by the user)."""
        facts = await self._deps.store.saved_in(session.id)
        threads = await self._deps.threads.touched_in(session.id)
        reminders = await self._deps.reminders.created_in(session.id)
        normal = Sensitivity.NORMAL
        lines = [f"Memory: {f.text}" for f in facts if f.sensitivity is normal]
        lines += [
            f"Storyline: {t.title} ({t.status.value.replace('_', ' ')})"
            for t in threads
            if t.sensitivity is normal
        ]
        lines += [
            f"Reminder: {r.title}" + (f" on {r.on:%d %b %Y}" if r.on else " (no date yet)")
            for r in reminders
        ]
        return lines
