from datetime import UTC, datetime, timedelta
from typing import Any

from baatein.domain.ids import new_id
from baatein.domain.models import Boundary, Fact, Reminder, Role, Session, Thread, Turn, User

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


def make_thread(user_id: str, title: str = "Reconnect with Ramesh", **overrides: Any) -> Thread:
    fields: dict[str, Any] = {"id": new_id(), "user_id": user_id, "title": title, "created_at": T0}
    return Thread(**(fields | overrides))


def make_boundary(user_id: str, topic: str = "painting", **overrides: Any) -> Boundary:
    fields: dict[str, Any] = {"id": new_id(), "user_id": user_id, "topic": topic, "created_at": T0}
    return Boundary(**(fields | overrides))


def make_fact(user_id: str, text: str = "likes chai", **overrides: Any) -> Fact:
    fields: dict[str, Any] = {"id": new_id(), "user_id": user_id, "text": text, "created_at": T0}
    return Fact(**(fields | overrides))


def make_reminder(user_id: str, title: str = "Ravi's birthday", **overrides: Any) -> Reminder:
    fields: dict[str, Any] = {"id": new_id(), "user_id": user_id, "title": title, "created_at": T0}
    return Reminder(**(fields | overrides))
