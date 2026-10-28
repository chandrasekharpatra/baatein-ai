import re
from dataclasses import dataclass, field, replace
from datetime import datetime

from baatein.domain.errors import DuplicateEmailError, DuplicateTurnError, NotFoundError
from baatein.domain.models import FinalizeStatus, FollowUp, FollowUpStatus, Session, Turn, User


@dataclass
class InMemoryState:
    users: dict[str, User] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    turns: list[Turn] = field(default_factory=list)
    followups: dict[str, FollowUp] = field(default_factory=dict)


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

    async def end(self, session_id: str, ended_at: datetime, reason: str | None) -> None:
        self._update(
            session_id,
            ended_at=ended_at,
            end_reason=reason,
            finalize_status=FinalizeStatus.PENDING,
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
        terms = set(_words(query))
        sessions = {s.id for s in self._state.sessions.values() if s.user_id == user_id}
        scored = [
            (len(terms & set(_words(t.text))), t)
            for t in self._state.turns
            if t.session_id in sessions
        ]
        matches = [(score, t) for score, t in scored if score]
        matches.sort(key=lambda pair: (-pair[0], -pair[1].created_at.timestamp()))
        return [t for _, t in matches][:limit]


class InMemoryFollowUpRepo:
    def __init__(self, state: InMemoryState) -> None:
        self._state = state

    async def add(self, followup: FollowUp) -> None:
        if followup.user_id not in self._state.users:
            raise NotFoundError("user or session", followup.user_id)
        self._state.followups[followup.id] = followup

    async def get(self, followup_id: str) -> FollowUp | None:
        return self._state.followups.get(followup_id)

    async def list_open(self, user_id: str, limit: int) -> list[FollowUp]:
        followups = [
            f
            for f in self._state.followups.values()
            if f.user_id == user_id and f.status is FollowUpStatus.OPEN
        ]
        followups.sort(
            key=lambda f: (
                f.due_at is None,
                f.due_at.timestamp() if f.due_at else 0,
                -f.priority,
                f.created_at,
            )
        )
        return followups[:limit]

    async def resolve(
        self, followup_id: str, status: FollowUpStatus, resolved_at: datetime
    ) -> None:
        if status is FollowUpStatus.OPEN:
            raise ValueError("a follow-up can only be resolved as done or dropped")
        if followup_id not in self._state.followups:
            raise NotFoundError("follow-up", followup_id)
        self._state.followups[followup_id] = replace(
            self._state.followups[followup_id], status=status, resolved_at=resolved_at
        )


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())
