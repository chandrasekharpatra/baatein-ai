from dataclasses import dataclass, field
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


class Pace(StrEnum):
    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"


class Verbosity(StrEnum):
    SHORT = "short"
    NORMAL = "normal"
    DETAILED = "detailed"


@dataclass(frozen=True)
class Preferences:
    """How the user asked Baatein to talk to them (#21 "itne sawaal mat poochho", #23 "dheere
    bolo"). None means the persona's default."""

    address_as: str | None = None
    pace: Pace | None = None
    verbosity: Verbosity | None = None
    fewer_questions: bool = False


@dataclass(frozen=True)
class User:
    id: str
    email: str
    password_hash: str
    created_at: datetime
    display_name: str | None = None
    language: str = "hinglish"
    timezone: str = "Asia/Kolkata"
    persona: str = "casual"
    preferences: Preferences = field(default_factory=Preferences)


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
    ended_abruptly: bool = False
    unfinished_topic: str | None = None


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


class FactCategory(StrEnum):
    PERSONAL = "personal"
    RELATIONSHIP = "relationship"
    WORK = "work"
    HEALTH = "health"
    PREFERENCE = "preference"
    EVENT = "event"
    OTHER = "other"


class MemoryKind(StrEnum):
    FACT = "fact"
    STORY = "story"
    PREFERENCE = "preference"
    MOMENT = "moment"


class Certainty(StrEnum):
    SURE = "sure"
    UNCERTAIN = "uncertain"


class Sensitivity(StrEnum):
    NORMAL = "normal"
    PERSONAL = "personal"
    HEALTH = "health"
    GRIEF = "grief"
    CRISIS = "crisis"


@dataclass(frozen=True)
class Person:
    id: str
    user_id: str
    name: str
    created_at: datetime
    relation: str | None = None


@dataclass(frozen=True)
class Fact:
    """A memory the user agreed to keep. `source_session_id` is the call where they agreed."""

    id: str
    user_id: str
    text: str
    created_at: datetime
    category: FactCategory = FactCategory.OTHER
    importance: int = 3
    source_session_id: str | None = None
    superseded_by: str | None = None
    retired_at: datetime | None = None
    kind: MemoryKind = MemoryKind.FACT
    quote: str | None = None
    about_person_id: str | None = None
    certainty: Certainty = Certainty.SURE
    sensitivity: Sensitivity = Sensitivity.NORMAL
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.importance <= 5:
            raise ValueError(f"importance must be between 1 and 5, got {self.importance}")

    @property
    def is_active(self) -> bool:
        return self.retired_at is None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(frozen=True)
class ScoredFact:
    fact: Fact
    score: float


class ThreadKind(StrEnum):
    RELATIONSHIP = "relationship"
    ASPIRATION = "aspiration"
    INTENTION = "intention"
    WORRY = "worry"
    PLAN = "plan"
    STORY = "story"


class ThreadStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    NOT_DONE = "not_done"
    CANCELLED = "cancelled"
    SNOOZED = "snoozed"


ACTIVE_THREAD_STATUSES = frozenset(
    {ThreadStatus.OPEN, ThreadStatus.IN_PROGRESS, ThreadStatus.SNOOZED}
)


@dataclass(frozen=True)
class Thread:
    """A storyline the user agreed Baatein can check in on, e.g. "Reconnect with Ramesh"."""

    id: str
    user_id: str
    title: str
    created_at: datetime
    kind: ThreadKind = ThreadKind.PLAN
    status: ThreadStatus = ThreadStatus.OPEN
    next_step: str | None = None
    check_in_after: datetime | None = None
    last_offered_at: datetime | None = None
    times_offered: int = 0
    sensitivity: Sensitivity = Sensitivity.NORMAL
    about_person_id: str | None = None
    source_session_id: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_THREAD_STATUSES


@dataclass(frozen=True)
class ThreadEvent:
    """Something that happened in a storyline, e.g. "Called Ramesh". `status` is the new one."""

    id: str
    thread_id: str
    text: str
    created_at: datetime
    status: ThreadStatus | None = None
    session_id: str | None = None


class BoundaryScope(StrEnum):
    THIS_CALL = "this_call"
    UNTIL_REOPENED = "until_reopened"
    UNTIL_DATE = "until_date"


@dataclass(frozen=True)
class Boundary:
    """A topic the user asked Baatein not to bring up. The memories about it stay."""

    id: str
    user_id: str
    topic: str
    created_at: datetime
    scope: BoundaryScope = BoundaryScope.UNTIL_REOPENED
    session_id: str | None = None
    until: datetime | None = None
    thread_id: str | None = None
    lifted_at: datetime | None = None

    def applies(self, session_id: str, now: datetime) -> bool:
        if self.lifted_at is not None:
            return False
        if self.scope is BoundaryScope.THIS_CALL:
            return self.session_id == session_id
        if self.scope is BoundaryScope.UNTIL_DATE:
            return self.until is not None and now < self.until
        return True


@dataclass(frozen=True)
class DeclinedSuggestion:
    """A suggestion the user didn't like (#29 "walk ki baat se naraz tha"); never repeated."""

    id: str
    user_id: str
    text: str
    created_at: datetime
    session_id: str | None = None
