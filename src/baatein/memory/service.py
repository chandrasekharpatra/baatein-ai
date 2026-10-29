import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime

from baatein.config import MemorySettings
from baatein.domain.ids import new_id
from baatein.domain.models import (
    Fact,
    FollowUp,
    FollowUpStatus,
    Role,
    ScoredFact,
    Session,
    Turn,
    User,
)
from baatein.ports.clock import Clock
from baatein.ports.embedder import Embedder
from baatein.ports.memory import MemoryStore
from baatein.ports.repos import FollowUpRepo, SessionRepo, TurnRepo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryContext:
    today: date
    summaries: list[Session]
    followups: list[FollowUp]
    facts: list[Fact]


@dataclass(frozen=True)
class SearchResult:
    facts: list[ScoredFact]
    turns: list[Turn]


@dataclass(frozen=True)
class MemoryDeps:
    sessions: SessionRepo
    turns: TurnRepo
    followups: FollowUpRepo
    store: MemoryStore
    clock: Clock
    settings: MemorySettings


class MemoryService:
    """Opens per-call memory. One instance per process."""

    def __init__(self, deps: MemoryDeps, embedder: Embedder) -> None:
        self._deps = deps
        self._embedder = embedder

    async def warm_up(self) -> None:
        """Load the embedding model so the first per-turn recall fits its timeout."""
        try:
            await self._embedder.embed(["warm up"], "query")
        except Exception:
            logger.exception("embedder warm-up failed")

    async def open_call(
        self, user: User, room_name: str | None = None, language: str | None = None
    ) -> "CallMemory":
        session = Session(
            id=new_id(), user_id=user.id, started_at=self._deps.clock.now(), room_name=room_name
        )
        await self._deps.sessions.add(session)
        return CallMemory(self._deps, session, language)


class CallMemory:
    """Memory for one call: its session row, transcript writes, recall and follow-ups."""

    def __init__(self, deps: MemoryDeps, session: Session, language: str | None = None) -> None:
        self._deps = deps
        self.session = session
        self._language = language
        self._seq = 0
        self._pending: asyncio.Future[None] | None = None
        self._closed = False

    @property
    def user_id(self) -> str:
        return self.session.user_id

    async def context(self) -> MemoryContext:
        """What to tell the agent at call start: recent summaries, open follow-ups, top facts."""
        limits = self._deps.settings
        recent, followups, facts = await asyncio.gather(
            self._deps.sessions.recent_for_user(self.user_id, limits.context_summaries + 1),
            self._deps.followups.list_open(self.user_id, limits.context_followups),
            self._deps.store.active(self.user_id, limits.context_facts),
        )
        summaries = [s for s in recent if s.summary and s.id != self.session.id]
        return MemoryContext(
            today=self._deps.clock.now().date(),
            summaries=summaries[: limits.context_summaries],
            followups=followups,
            facts=facts,
        )

    def record(self, role: Role, text: str, interrupted: bool = False) -> None:
        """Queue a turn for writing. Turns are numbered and written in the order recorded."""
        if self._closed:
            logger.warning("dropping turn recorded after session %s closed", self.session.id)
            return
        self._seq += 1
        turn = Turn(
            session_id=self.session.id,
            seq=self._seq,
            role=role,
            text=text,
            created_at=self._deps.clock.now(),
            language=self._language,
            interrupted=interrupted,
        )
        self._pending = asyncio.ensure_future(self._append_after(self._pending, turn))

    async def _append_after(self, previous: asyncio.Future[None] | None, turn: Turn) -> None:
        if previous is not None:
            await previous
        try:
            await self._deps.turns.append(turn)
        except Exception:
            logger.exception("failed to save turn %d of session %s", turn.seq, turn.session_id)

    async def flush(self) -> None:
        if self._pending is not None:
            await self._pending

    async def recall(self, query: str) -> list[ScoredFact]:
        """Facts relevant to `query`, or nothing if the search is slow or fails."""
        settings = self._deps.settings
        try:
            return await asyncio.wait_for(
                self._deps.store.search(self.user_id, query, settings.recall_limit),
                settings.recall_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("memory recall timed out after %.2fs", settings.recall_timeout_seconds)
        except Exception:
            logger.exception("memory recall failed")
        return []

    async def search(self, query: str) -> SearchResult:
        """Facts and earlier calls' transcript lines matching `query`, for an explicit lookup."""
        limit = self._deps.settings.recall_limit
        facts, turns = await asyncio.gather(
            self._deps.store.search(self.user_id, query, limit),
            self._deps.turns.search(self.user_id, query, limit),
        )
        earlier = [t for t in turns if t.session_id != self.session.id]
        return SearchResult(facts=facts, turns=earlier)

    async def add_followup(self, text: str, due_at: datetime | None = None) -> FollowUp:
        followup = FollowUp(
            id=new_id(),
            user_id=self.user_id,
            text=text,
            created_at=self._deps.clock.now(),
            due_at=due_at,
            source_session_id=self.session.id,
        )
        await self._deps.followups.add(followup)
        return followup

    async def resolve_followup(self, followup_id: str, status: FollowUpStatus) -> FollowUp | None:
        """Close one of this user's open follow-ups. Returns None if there is no such follow-up."""
        followups = self._deps.followups
        followup = await followups.get(followup_id)
        if (
            followup is None
            or followup.user_id != self.user_id
            or followup.status is not FollowUpStatus.OPEN
        ):
            return None
        await followups.resolve(followup_id, status, self._deps.clock.now())
        return await followups.get(followup_id)

    async def close(self, reason: str | None = None) -> None:
        """Wait for pending turn writes, then end the session (ready for finalization)."""
        if self._closed:
            return
        self._closed = True
        await self.flush()
        await self._deps.sessions.end(self.session.id, self._deps.clock.now(), reason)
