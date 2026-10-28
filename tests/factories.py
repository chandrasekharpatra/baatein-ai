from datetime import UTC, datetime, timedelta
from typing import Any

from baatein.domain.ids import new_id
from baatein.domain.models import Fact, FollowUp, Role, Session, Turn, User

T0 = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def make_user(**overrides: Any) -> User:
    fields: dict[str, Any] = {
        "id": new_id(),
        "email": f"{new_id().lower()}@example.com",
        "password_hash": "hash",
        "created_at": T0,
    }
    return User(**(fields | overrides))


def make_session(user_id: str, **overrides: Any) -> Session:
    fields: dict[str, Any] = {"id": new_id(), "user_id": user_id, "started_at": T0}
    return Session(**(fields | overrides))


def make_turn(session_id: str, seq: int, text: str = "hello", **overrides: Any) -> Turn:
    fields: dict[str, Any] = {
        "session_id": session_id,
        "seq": seq,
        "role": Role.USER,
        "text": text,
        "created_at": at(seq),
    }
    return Turn(**(fields | overrides))


def make_followup(user_id: str, **overrides: Any) -> FollowUp:
    fields: dict[str, Any] = {"id": new_id(), "user_id": user_id, "text": "ask", "created_at": T0}
    return FollowUp(**(fields | overrides))


def make_fact(user_id: str, text: str = "likes chai", **overrides: Any) -> Fact:
    fields: dict[str, Any] = {"id": new_id(), "user_id": user_id, "text": text, "created_at": T0}
    return Fact(**(fields | overrides))
