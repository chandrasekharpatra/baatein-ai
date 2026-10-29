import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime

from baatein.config import MemorySettings
from baatein.domain.ids import new_id
from baatein.domain.models import (
    Certainty,
    Fact,
    FollowUp,
    FollowUpStatus,
    MemoryKind,
    Person,
    Role,
    ScoredFact,
    Sensitivity,
    Session,
    Turn,
    User,
)
from baatein.memory.redact import redact
from baatein.ports.clock import Clock
from baatein.ports.embedder import Embedder
from baatein.ports.memory import MemoryStore
from baatein.ports.repos import FollowUpRepo, PeopleRepo, SessionRepo, TurnRepo

logger = logging.getLogger(__name__)

FORGOTTEN_TURN = "[forgotten at the user's request]"
CONTEXT_PEOPLE = 20


@dataclass(frozen=True)
class MemoryContext:
    today: date
    summaries: list[Session]
    followups: list[FollowUp]
    facts: list[Fact]
    people: list[Person]


@dataclass(frozen=True)
class MemoryDeps:
    sessions: SessionRepo
    turns: TurnRepo
    followups: FollowUpRepo
    people: PeopleRepo
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
        self.saving_paused = False

    @property
    def user_id(self) -> str:
        return self.session.user_id

    async def context(self) -> MemoryContext:
        """What to tell the agent at call start: recent summaries, open follow-ups, top facts."""
        limits = self._deps.settings
        recent, followups, facts, people = await asyncio.gather(
            self._deps.sessions.recent_for_user(self.user_id, limits.context_summaries + 1),
            self._deps.followups.list_open(self.user_id, limits.context_followups),
            self._deps.store.active(self.user_id, limits.context_facts),
            self._deps.people.list_for_user(self.user_id, CONTEXT_PEOPLE),
        )
        summaries = [s for s in recent if s.summary and s.id != self.session.id]
        return MemoryContext(
            today=self._deps.clock.now().date(),
            summaries=summaries[: limits.context_summaries],
            followups=followups,
            facts=facts,
            people=people,
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
            text=redact(text),
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

    async def search(self, query: str) -> list[ScoredFact]:
        """Saved memories matching `query`, for an explicit lookup. Never raw transcripts."""
        return await self._deps.store.search(self.user_id, query, self._deps.settings.recall_limit)

    def pause_saving(self) -> None:
        """The user asked not to save anything from this call ("save mat karna")."""
        self.saving_paused = True

    async def remember(
        self,
        text: str,
        *,
        kind: MemoryKind = MemoryKind.FACT,
        quote: str | None = None,
        about: str | None = None,
        relation: str | None = None,
        sensitivity: Sensitivity = Sensitivity.NORMAL,
        certainty: Certainty = Certainty.SURE,
    ) -> Fact | None:
        """Save a memory the user has just agreed to. Returns None while saving is paused."""
        if self.saving_paused:
            return None
        person = await self.person(about, relation) if about else None
        fact = Fact(
            id=new_id(),
            user_id=self.user_id,
            text=redact(text),
            created_at=self._deps.clock.now(),
            source_session_id=self.session.id,
            kind=kind,
            quote=redact(quote) if quote else None,
            about_person_id=person.id if person else None,
            certainty=certainty,
            sensitivity=sensitivity,
        )
        await self._deps.store.add(fact)
        return fact

    async def correct(self, fact_id: str, text: str, quote: str | None = None) -> Fact | None:
        """Replace one of the user's current memories. Returns None if there is no such memory."""
        old = await self._own_active(fact_id)
        if old is None:
            return None
        replacement = Fact(
            id=new_id(),
            user_id=self.user_id,
            text=redact(text),
            created_at=self._deps.clock.now(),
            source_session_id=self.session.id,
            kind=old.kind,
            quote=redact(quote) if quote else old.quote,
            about_person_id=old.about_person_id,
            certainty=old.certainty,
            sensitivity=old.sensitivity,
        )
        await self._deps.store.supersede(fact_id, replacement)
        return replacement

    async def forget(self, fact_id: str) -> bool:
        """Tombstone a memory and its earlier versions, and redact the calls it came from."""
        fact = await self._deps.store.get(fact_id)
        if fact is None or fact.user_id != self.user_id or fact.is_deleted:
            return False
        await self.flush()
        for session_id in await self._deps.store.forget(fact_id, self._deps.clock.now()):
            await self._deps.turns.redact_session(session_id, FORGOTTEN_TURN)
            await self._deps.sessions.clear_summary(session_id)
        person_id = fact.about_person_id
        if person_id and await self._deps.store.count_about(person_id) == 0:
            await self._deps.people.delete(person_id)
        return True

    async def people(self) -> list[Person]:
        return await self._deps.people.list_for_user(self.user_id, CONTEXT_PEOPLE)

    async def person(self, name: str, relation: str | None = None) -> Person:
        """The user's person with this name, created on first mention."""
        people = self._deps.people
        found = await people.find_by_name(self.user_id, name)
        if found is not None:
            if relation and found.relation != relation:
                found = Person(found.id, found.user_id, found.name, found.created_at, relation)
                await people.update(found)
            return found
        person = Person(
            id=new_id(),
            user_id=self.user_id,
            name=name.strip(),
            created_at=self._deps.clock.now(),
            relation=relation,
        )
        await people.add(person)
        return person

    async def rename_person(self, name: str, new_name: str) -> Person | None:
        """Fix a person's name ("Suman nahi, Sunita"). Returns None if there is no such person."""
        found = await self._deps.people.find_by_name(self.user_id, name)
        if found is None:
            return None
        renamed = Person(
            found.id, found.user_id, new_name.strip(), found.created_at, found.relation
        )
        await self._deps.people.update(renamed)
        return renamed

    async def _own_active(self, fact_id: str) -> Fact | None:
        fact = await self._deps.store.get(fact_id)
        if fact is None or fact.user_id != self.user_id or not fact.is_active:
            return None
        return fact

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
