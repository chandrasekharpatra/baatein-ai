from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class FinalizeStatus(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class FollowUpStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    DROPPED = "dropped"


@dataclass(frozen=True)
class User:
    id: str
    email: str
    password_hash: str
    created_at: datetime
    display_name: str | None = None
    language: str = "hinglish"


@dataclass(frozen=True)
class Session:
    id: str
    user_id: str
    started_at: datetime
    room_name: str | None = None
    ended_at: datetime | None = None
    end_reason: str | None = None
    summary: str | None = None
    finalize_status: FinalizeStatus = FinalizeStatus.ACTIVE
    finalize_attempts: int = 0
    memory_synced_seq: int = 0


@dataclass(frozen=True)
class Turn:
    session_id: str
    seq: int
    role: Role
    text: str
    created_at: datetime
    language: str | None = None
    interrupted: bool = False
    id: int | None = None


@dataclass(frozen=True)
class FollowUp:
    id: str
    user_id: str
    text: str
    created_at: datetime
    due_at: datetime | None = None
    status: FollowUpStatus = FollowUpStatus.OPEN
    priority: int = 0
    source_session_id: str | None = None
    resolved_at: datetime | None = None


class FactCategory(StrEnum):
    PERSONAL = "personal"
    RELATIONSHIP = "relationship"
    WORK = "work"
    HEALTH = "health"
    PREFERENCE = "preference"
    EVENT = "event"
    OTHER = "other"


@dataclass(frozen=True)
class Fact:
    id: str
    user_id: str
    text: str
    created_at: datetime
    category: FactCategory = FactCategory.OTHER
    importance: int = 3
    source_session_id: str | None = None
    superseded_by: str | None = None
    retired_at: datetime | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.importance <= 5:
            raise ValueError(f"importance must be between 1 and 5, got {self.importance}")

    @property
    def is_active(self) -> bool:
        return self.retired_at is None


@dataclass(frozen=True)
class ScoredFact:
    fact: Fact
    score: float
