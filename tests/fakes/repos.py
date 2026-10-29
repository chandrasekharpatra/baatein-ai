from dataclasses import dataclass, field, replace
from datetime import datetime

from baatein.domain.errors import (
    DuplicateEmailError,
    DuplicatePersonError,
    DuplicateTurnError,
    NotFoundError,
)
from baatein.domain.models import (
    Boundary,
    DeclinedSuggestion,
    FinalizeStatus,
    Person,
    Session,
    Thread,
    ThreadEvent,
    Turn,
    User,
)
from baatein.text import search_terms, words


@dataclass
class InMemoryState:
    users: dict[str, User] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    turns: list[Turn] = field(default_factory=list)
    people: dict[str, Person] = field(default_factory=dict)
    threads: dict[str, Thread] = field(default_factory=dict)
    thread_events: list[ThreadEvent] = field(default_factory=list)
    boundaries: dict[str, Boundary] = field(default_factory=dict)
    suggestions: list[DeclinedSuggestion] = field(default_factory=list)


class InMemoryUserRepo:
    def __init__(self, state: InMemoryState) -> None:
        self._state = state

    async def add(self, user: User) -> None:
        if await self.get_by_email(user.email) is not None:
            raise DuplicateEmailError(user.email)
        self._state.users[user.id] = user

    async def get(self, user_id: str) -> User | None:
        return self._state.users.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        return next(
            (u for u in self._state.users.values() if u.email.lower() == email.lower()), None
        )


class InMemorySessionRepo:
    def __init__(self, state: InMemoryState) -> None:
        self._state = state

    async def add(self, session: Session) -> None:
        if session.user_id not in self._state.users:
            raise NotFoundError("user", session.user_id)
        self._state.sessions[session.id] = session

    async def get(self, session_id: str) -> Session | None:
        return self._state.sessions.get(session_id)

    async def recent_for_user(self, user_id: str, limit: int) -> list[Session]:
        sessions = [s for s in self._state.sessions.values() if s.user_id == user_id]
        return sorted(sessions, key=lambda s: s.started_at, reverse=True)[:limit]

    async def end(
        self,
        session_id: str,
        ended_at: datetime,
        reason: str | None,
        ended_abruptly: bool = False,
        unfinished_topic: str | None = None,
    ) -> None:
        self._update(
            session_id,
            ended_at=ended_at,
            end_reason=reason,
            finalize_status=FinalizeStatus.PENDING,
            ended_abruptly=ended_abruptly,
            unfinished_topic=unfinished_topic,
        )

    async def set_memory_synced_seq(self, session_id: str, seq: int) -> None:
        self._update(session_id, memory_synced_seq=seq)

    async def mark_finalized(self, session_id: str, summary: str | None) -> None:
        self._update(session_id, finalize_status=FinalizeStatus.DONE, summary=summary)

    async def mark_finalize_failed(self, session_id: str) -> None:
        session = self._get(session_id)
        self._update(
            session_id,
            finalize_status=FinalizeStatus.FAILED,
            finalize_attempts=session.finalize_attempts + 1,
        )

    async def pending_finalization(self, max_attempts: int, limit: int) -> list[Session]:
        sessions = [
            s
            for s in self._state.sessions.values()
            if s.finalize_status in (FinalizeStatus.PENDING, FinalizeStatus.FAILED)
            and s.finalize_attempts < max_attempts
        ]
        return sorted(sessions, key=lambda s: (s.ended_at is not None, s.ended_at, s.id))[:limit]

    async def clear_summary(self, session_id: str) -> None:
        self._update(session_id, summary=None, unfinished_topic=None)

    def _get(self, session_id: str) -> Session:
        try:
            return self._state.sessions[session_id]
        except KeyError:
            raise NotFoundError("session", session_id) from None

    def _update(self, session_id: str, **changes: object) -> None:
        self._state.sessions[session_id] = replace(self._get(session_id), **changes)  # type: ignore[arg-type]


class InMemoryTurnRepo:
    def __init__(self, state: InMemoryState) -> None:
        self._state = state

    async def append(self, turn: Turn) -> Turn:
        if turn.session_id not in self._state.sessions:
            raise NotFoundError("session", turn.session_id)
        if any(t.session_id == turn.session_id and t.seq == turn.seq for t in self._state.turns):
            raise DuplicateTurnError(turn.session_id, turn.seq)
        stored = replace(turn, id=len(self._state.turns) + 1)
        self._state.turns.append(stored)
        return stored

    async def list_for_session(self, session_id: str, after_seq: int = 0) -> list[Turn]:
        turns = [t for t in self._state.turns if t.session_id == session_id and t.seq > after_seq]
        return sorted(turns, key=lambda t: t.seq)

    async def search(self, user_id: str, query: str, limit: int) -> list[Turn]:
        terms = set(search_terms(query))
        sessions = {s.id for s in self._state.sessions.values() if s.user_id == user_id}
        scored = [
            (len(terms & set(words(t.text))), t)
            for t in self._state.turns
            if t.session_id in sessions
        ]
        matches = [(score, t) for score, t in scored if score]
        matches.sort(key=lambda pair: (-pair[0], -pair[1].created_at.timestamp()))
        return [t for _, t in matches][:limit]

    async def redact_session(self, session_id: str, text: str) -> int:
        changed = 0
        for index, turn in enumerate(self._state.turns):
            if turn.session_id == session_id:
                self._state.turns[index] = replace(turn, text=text)
                changed += 1
        return changed


class InMemoryPeopleRepo:
    def __init__(self, state: InMemoryState) -> None:
        self._state = state

    async def add(self, person: Person) -> None:
        if person.user_id not in self._state.users:
            raise NotFoundError("user", person.user_id)
        if await self.find_by_name(person.user_id, person.name) is not None:
            raise DuplicatePersonError(person.name)
        self._state.people[person.id] = person

    async def get(self, person_id: str) -> Person | None:
        return self._state.people.get(person_id)

    async def find_by_name(self, user_id: str, name: str) -> Person | None:
        return next(
            (
                p
                for p in self._state.people.values()
                if p.user_id == user_id and p.name.lower() == name.strip().lower()
            ),
            None,
        )

    async def list_for_user(self, user_id: str, limit: int) -> list[Person]:
        people = [p for p in self._state.people.values() if p.user_id == user_id]
        return sorted(people, key=lambda p: (p.created_at, p.name))[:limit]

    async def update(self, person: Person) -> None:
        if person.id not in self._state.people:
            raise NotFoundError("person", person.id)
        clash = await self.find_by_name(person.user_id, person.name)
        if clash is not None and clash.id != person.id:
            raise DuplicatePersonError(person.name)
        self._state.people[person.id] = person

    async def delete(self, person_id: str) -> None:
        self._state.people.pop(person_id, None)


class InMemoryThreadRepo:
    def __init__(self, state: InMemoryState) -> None:
        self._state = state

    async def add(self, thread: Thread) -> None:
        if thread.user_id not in self._state.users:
            raise NotFoundError("user, person or session", thread.user_id)
        self._state.threads[thread.id] = thread

    async def get(self, thread_id: str) -> Thread | None:
        return self._state.threads.get(thread_id)

    async def update(self, thread: Thread) -> None:
        if thread.id not in self._state.threads:
            raise NotFoundError("thread", thread.id)
        self._state.threads[thread.id] = thread

    async def list_active(self, user_id: str, limit: int) -> list[Thread]:
        threads = [t for t in self._state.threads.values() if t.user_id == user_id and t.is_active]
        return sorted(threads, key=lambda t: (t.created_at, t.id))[:limit]

    async def add_event(self, event: ThreadEvent) -> None:
        if event.thread_id not in self._state.threads:
            raise NotFoundError("thread or session", event.thread_id)
        self._state.thread_events.append(event)

    async def events(self, thread_id: str, limit: int) -> list[ThreadEvent]:
        events = [e for e in self._state.thread_events if e.thread_id == thread_id]
        events.sort(key=lambda e: (e.created_at, e.id))
        return events[-limit:] if limit else []

    async def count_about(self, person_id: str) -> int:
        return sum(1 for t in self._state.threads.values() if t.about_person_id == person_id)

    async def delete(self, thread_id: str) -> list[str]:
        thread = self._state.threads.pop(thread_id, None)
        events = [e for e in self._state.thread_events if e.thread_id == thread_id]
        self._state.thread_events = [e for e in self._state.thread_events if e not in events]
        for boundary in [b for b in self._state.boundaries.values() if b.thread_id == thread_id]:
            del self._state.boundaries[boundary.id]
        sessions = {e.session_id for e in events} | {thread and thread.source_session_id}
        return sorted(s for s in sessions if s)


class InMemoryBoundaryRepo:
    def __init__(self, state: InMemoryState) -> None:
        self._state = state

    async def add(self, boundary: Boundary) -> None:
        if boundary.user_id not in self._state.users:
            raise NotFoundError("user, session or thread", boundary.user_id)
        self._state.boundaries[boundary.id] = boundary

    async def get(self, boundary_id: str) -> Boundary | None:
        return self._state.boundaries.get(boundary_id)

    async def list_active(self, user_id: str, session_id: str, now: datetime) -> list[Boundary]:
        boundaries = [
            b
            for b in self._state.boundaries.values()
            if b.user_id == user_id and b.applies(session_id, now)
        ]
        return sorted(boundaries, key=lambda b: (b.created_at, b.id))

    async def lift(self, boundary_id: str, at: datetime) -> None:
        boundary = self._state.boundaries.get(boundary_id)
        if boundary is None:
            raise NotFoundError("boundary", boundary_id)
        self._state.boundaries[boundary_id] = replace(boundary, lifted_at=boundary.lifted_at or at)


class InMemorySuggestionRepo:
    def __init__(self, state: InMemoryState) -> None:
        self._state = state

    async def add(self, suggestion: DeclinedSuggestion) -> None:
        if suggestion.user_id not in self._state.users:
            raise NotFoundError("user or session", suggestion.user_id)
        self._state.suggestions.append(suggestion)

    async def list_for_user(self, user_id: str, limit: int) -> list[DeclinedSuggestion]:
        found = [s for s in self._state.suggestions if s.user_id == user_id]
        found.sort(key=lambda s: (s.created_at, s.id), reverse=True)
        return found[:limit]
