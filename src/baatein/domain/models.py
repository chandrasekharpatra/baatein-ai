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
