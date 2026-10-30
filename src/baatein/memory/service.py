import asyncio
import logging
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from baatein.config import MemorySettings
from baatein.domain.ids import new_id
from baatein.domain.models import (
    REMINDER_TIME,
    Boundary,
    BoundaryScope,
    Certainty,
    DeclinedSuggestion,
    Fact,
    MemoryKind,
    OccurrenceStatus,
    Pace,
    Person,
    Preferences,
    Reminder,
    ReminderStatus,
    Repeat,
    Role,
    ScoredFact,
    Sensitivity,
    Session,
    Thread,
    ThreadEvent,
    ThreadKind,
    ThreadStatus,
    Turn,
    User,
    Verbosity,
)
from baatein.memory.ranker import CheckInRules, pick_check_in
from baatein.memory.recurrence import due, nearest_open
from baatein.memory.redact import redact
from baatein.ports.clock import Clock
from baatein.ports.embedder import Embedder
from baatein.ports.memory import MemoryStore
from baatein.ports.repos import (
    BoundaryRepo,
    PeopleRepo,
    ReminderRepo,
    SessionRepo,
    SuggestionRepo,
    ThreadRepo,
    TurnRepo,
    UserRepo,
)
from baatein.text import search_terms, words

logger = logging.getLogger(__name__)

FORGOTTEN_TURN = "[forgotten at the user's request]"
CONTEXT_PEOPLE = 20
CHECK_IN_EVENTS = 3
DEFAULT_TIMEZONE = "Asia/Kolkata"
# A call whose last turn is the user saying this much, with no reply, was cut off mid-story.
ABRUPT_MIN_WORDS = 4
MAX_TOPIC_CHARS = 200


def _alerts(days_before: tuple[int, ...]) -> tuple[int, ...]:
    """Distinct alert offsets from 0 to 60 days, largest first; same day if none are valid."""
    valid = sorted({d for d in days_before if 0 <= d <= 60}, reverse=True)
    return tuple(valid) or (0,)


def zone(name: str) -> ZoneInfo:
    """The user's timezone, or India's if the stored name is unknown."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown timezone %r, using %s", name, DEFAULT_TIMEZONE)
        return ZoneInfo(DEFAULT_TIMEZONE)


CONTEXT_REMINDERS = 20


@dataclass(frozen=True)
class DueReminder:
    reminder: Reminder
    on: date
    alerted: bool
    """An alert time passed since the last call: bring it up now."""


@dataclass(frozen=True)
class MemoryContext:
    now: datetime
    """In the user's timezone."""
    last_call: Session | None
    facts: list[Fact]
    people: list[Person]
    threads: list[Thread]
    check_in: Thread | None
    check_in_events: list[ThreadEvent]
    boundaries: list[Boundary]
    declined: list[DeclinedSuggestion]
    reminders: list[DueReminder] = field(default_factory=list)
    undated_reminders: list[Reminder] = field(default_factory=list)
    later_reminders: list[Reminder] = field(default_factory=list)
    """Active reminders with nothing due in the next week: listed so they can be changed."""


class ForgetOutcome(StrEnum):
    DELETED = "deleted"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class MemoryDeps:
    users: UserRepo
    sessions: SessionRepo
    turns: TurnRepo
    people: PeopleRepo
    threads: ThreadRepo
    boundaries: BoundaryRepo
    suggestions: SuggestionRepo
    reminders: ReminderRepo
    store: MemoryStore
    clock: Clock
    settings: MemorySettings


def _consume_error(task: "asyncio.Task[list[ScoredFact]]") -> None:
    """Mark a search's error as seen; `recall` logs the ones anyone waits for."""
    if not task.cancelled():
        task.exception()


class MemoryService:
    """Opens per-call memory. One instance per process."""

    def __init__(
        self,
        deps: MemoryDeps,
        embedder: Embedder,
        cut_off: Callable[[str | None], float] | None = None,
    ) -> None:
        self._deps = deps
        self._embedder = embedder
        self._cut_off = cut_off

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
        cut_off = self._cut_off
        return CallMemory(
            self._deps,
            session,
            language,
            zone(user.timezone),
            user.preferences,
            turn_cut_off=cut_off(language or user.language) if cut_off else None,
            tool_cut_off=cut_off(None) if cut_off else None,
        )


class CallMemory:
    """Memory for one call: its session row, transcript writes, recall, storylines and
    boundaries."""

    def __init__(
        self,
        deps: MemoryDeps,
        session: Session,
        language: str | None = None,
        timezone: ZoneInfo | None = None,
        preferences: Preferences | None = None,
        turn_cut_off: float | None = None,
        tool_cut_off: float | None = None,
    ) -> None:
        self._deps = deps
        self._turn_cut_off = turn_cut_off
        """Similarity cut-off for the user's own words (it depends on their language)."""
        self._tool_cut_off = tool_cut_off
        """Similarity cut-off for the recall tool's English keywords."""
        self.preferences = preferences or Preferences()
        self.session = session
        self._language = language
        self.timezone = timezone or ZoneInfo(DEFAULT_TIMEZONE)
        self._seq = 0
        self._pending: asyncio.Future[None] | None = None
        self._closed = False
        self._last_turn: Turn | None = None
        self.saving_paused = False
        self.private = False
        self._forget_requests: dict[str, int] = {}
        self.check_in: Thread | None = None
        self._check_in_terms: frozenset[str] = frozenset()

    @property
    def user_id(self) -> str:
        return self.session.user_id

    @property
    def settings(self) -> MemorySettings:
        return self._deps.settings

    async def context(self) -> MemoryContext:
        """What to tell the agent at call start. Marks the chosen check-in as offered."""
        deps, limits = self._deps, self._deps.settings
        now = deps.clock.now()
        recent, facts, people, threads, boundaries, declined = await asyncio.gather(
            deps.sessions.recent_for_user(self.user_id, 2),
            deps.store.active(self.user_id, limits.context_facts),
            deps.people.list_for_user(self.user_id, CONTEXT_PEOPLE),
            deps.threads.list_active(self.user_id, limits.context_threads),
            deps.boundaries.list_active(self.user_id, self.session.id, now),
            deps.suggestions.list_for_user(self.user_id, limits.context_declined),
        )
        reminders = await deps.reminders.list_open(self.user_id, CONTEXT_REMINDERS)
        last_call = next((s for s in recent if s.id != self.session.id), None)
        since = last_call.started_at if last_call else now - timedelta(days=1)
        due_reminders = await self._due_reminders(reminders, since, now)
        check_in, events = None, []
        # A story cut off last time is this call's one check-in; storylines wait.
        if last_call is None or not (last_call.ended_abruptly and last_call.unfinished_topic):
            rules = CheckInRules(
                timedelta(days=limits.check_in_gap_days), limits.check_in_max_offers
            )
            check_in = pick_check_in(threads, boundaries, now, rules)
        if check_in is not None:
            check_in = replace(
                check_in, last_offered_at=now, times_offered=check_in.times_offered + 1
            )
            await deps.threads.update(check_in)
            events = await deps.threads.events(check_in.id, CHECK_IN_EVENTS)
            person = next((p for p in people if p.id == check_in.about_person_id), None)
            self.check_in = check_in
            self._check_in_terms = frozenset(
                words(person.name) if person else search_terms(check_in.title)
            )
        return MemoryContext(
            now=now.astimezone(self.timezone),
            last_call=last_call,
            facts=facts,
            people=people,
            threads=threads,
            check_in=check_in,
            check_in_events=events,
            boundaries=boundaries,
            declined=declined,
            reminders=due_reminders,
            undated_reminders=[r for r in reminders if r.status is ReminderStatus.NEEDS_INFO],
            later_reminders=[
                r
                for r in reminders
                if r.status is ReminderStatus.ACTIVE
                and all(d.reminder.id != r.id for d in due_reminders)
            ],
        )

    def mentions_check_in(self, reply: str) -> bool:
        """Whether Baatein's reply brought up this call's check-in: it names the person the
        storyline is about, or (without one) two words of its title."""
        if not self._check_in_terms:
            return False
        said = set(words(reply)) & self._check_in_terms
        return len(said) >= min(2, len(self._check_in_terms))

    async def _due_reminders(
        self, reminders: list[Reminder], since: datetime, now: datetime
    ) -> list[DueReminder]:
        found = []
        for reminder in reminders:
            if reminder.status is not ReminderStatus.ACTIVE:
                continue
            settled = await self._deps.reminders.settled(reminder.id)
            for day, alerted in due(reminder, settled, since, now, self.timezone):
                found.append(DueReminder(reminder, day, alerted))
        return sorted(found, key=lambda d: (not d.alerted, d.on))

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
        self._last_turn = turn
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

    def start_recall(self, query: str) -> "asyncio.Task[list[ScoredFact]]":
        """Start searching for memories that match the user's words. The agent starts it on
        the final transcript, a moment before the turn ends."""
        started = time_module.perf_counter()
        task = asyncio.ensure_future(
            self._deps.store.search(
                self.user_id, query, self._deps.settings.recall_limit, self._turn_cut_off
            )
        )
        task.add_done_callback(_consume_error)

        def log_duration(done: "asyncio.Task[list[ScoredFact]]") -> None:
            if not done.cancelled():
                took = (time_module.perf_counter() - started) * 1000
                logger.info("memory recall: the search took %.0f ms", took)

        task.add_done_callback(log_duration)
        return task

    async def recall(
        self, query: "str | asyncio.Task[list[ScoredFact]]"
    ) -> list[ScoredFact] | None:
        """Facts relevant to the user's words, if the search finishes within the reply's short
        wait (`recall_timeout_seconds`). Otherwise None: the search carries on, and the caller
        can use what it finds on a later turn. [] if the search fails. Pass a task from
        `start_recall` to wait for a search already under way.

        A hosted embedder takes about 450 ms, so its searches usually miss the wait; holding
        every reply for it would make each one that much slower."""
        task = self.start_recall(query) if isinstance(query, str) else query
        timeout = self._deps.settings.recall_timeout_seconds
        started = time_module.perf_counter()
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            logger.info("memory recall: still searching; replying without it")
            return None
        if task.cancelled():
            return []
        if (error := task.exception()) is not None:
            logger.error("memory recall failed", exc_info=error)
            return []
        logger.info(
            "memory recall: the reply waited %.0f ms", (time_module.perf_counter() - started) * 1000
        )
        return task.result()

    async def search(self, query: str) -> list[ScoredFact]:
        """Saved memories matching `query`, for an explicit lookup. Never raw transcripts."""
        return await self._deps.store.search(
            self.user_id, query, self._deps.settings.recall_limit, self._tool_cut_off
        )

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

    async def forget(self, fact_id: str, user_turn: int) -> ForgetOutcome:
        """Delete a memory in two steps, so it is never lost to a single misheard sentence.

        `user_turn` is how many messages the user has sent so far in this call. The first call
        only records the request; a second call on the user's next message (their confirmation)
        deletes it. A request not confirmed on that next message expires.
        """
        fact = await self._deps.store.get(fact_id)
        if fact is None or fact.user_id != self.user_id or fact.is_deleted:
            return ForgetOutcome.NOT_FOUND
        if not self._confirmed(fact_id, user_turn):
            return ForgetOutcome.NEEDS_CONFIRMATION
        await self.flush()
        await self._redact_calls(await self._deps.store.forget(fact_id, self._deps.clock.now()))
        await self._drop_person_if_unused(fact.about_person_id)
        return ForgetOutcome.DELETED

    async def forget_thread(self, thread_id: str, user_turn: int) -> ForgetOutcome:
        """Delete a storyline, its events and its boundaries, confirmed like `forget`."""
        thread = await self._own_thread(thread_id)
        if thread is None:
            return ForgetOutcome.NOT_FOUND
        if not self._confirmed(thread_id, user_turn):
            return ForgetOutcome.NEEDS_CONFIRMATION
        await self.flush()
        await self._redact_calls(await self._deps.threads.delete(thread_id))
        await self._drop_person_if_unused(thread.about_person_id)
        return ForgetOutcome.DELETED

    def _confirmed(self, item_id: str, user_turn: int) -> bool:
        """True on the user's message right after the request; otherwise records the request."""
        if self._forget_requests.get(item_id) == user_turn - 1:
            del self._forget_requests[item_id]
            return True
        self._forget_requests[item_id] = user_turn
        return False

    async def _redact_calls(self, session_ids: list[str]) -> None:
        """Wipe the transcripts and summaries of calls where something forgotten was said."""
        for session_id in session_ids:
            await self._deps.turns.redact_session(session_id, FORGOTTEN_TURN)
            await self._deps.sessions.clear_summary(session_id)
            if session_id == self.session.id:
                self._last_turn = None

    async def _drop_person_if_unused(self, person_id: str | None) -> None:
        if (
            person_id
            and await self._deps.store.count_about(person_id) == 0
            and await self._deps.threads.count_about(person_id) == 0
            and await self._deps.reminders.count_about(person_id) == 0
        ):
            await self._deps.people.delete(person_id)

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

    async def _own_thread(self, thread_id: str) -> Thread | None:
        thread = await self._deps.threads.get(thread_id)
        return thread if thread is not None and thread.user_id == self.user_id else None

    async def track_thread(
        self,
        title: str,
        *,
        kind: ThreadKind = ThreadKind.PLAN,
        next_step: str | None = None,
        check_in_after: datetime | None = None,
        about: str | None = None,
        relation: str | None = None,
        sensitivity: Sensitivity = Sensitivity.NORMAL,
    ) -> Thread | None:
        """Start a storyline the user agreed to be asked about. None while saving is paused."""
        if self.saving_paused:
            return None
        person = await self.person(about, relation) if about else None
        thread = Thread(
            id=new_id(),
            user_id=self.user_id,
            title=redact(title),
            created_at=self._deps.clock.now(),
            kind=kind,
            next_step=redact(next_step) if next_step else None,
            check_in_after=check_in_after,
            sensitivity=sensitivity,
            about_person_id=person.id if person else None,
            source_session_id=self.session.id,
        )
        await self._deps.threads.add(thread)
        return thread

    async def update_thread(
        self,
        thread_id: str,
        *,
        event: str | None = None,
        status: ThreadStatus | None = None,
        next_step: str | None = None,
        check_in_after: datetime | None = None,
    ) -> Thread | None:
        """Record what happened in one of the user's storylines ("Maine unko phone kar diya").

        Returns None while saving is paused or if there is no such thread. The user answered,
        so the count of unanswered check-ins starts again.
        """
        if self.saving_paused:
            return None
        thread = await self._own_thread(thread_id)
        if thread is None:
            return None
        now = self._deps.clock.now()
        updated = replace(
            thread,
            status=status or thread.status,
            next_step=redact(next_step) if next_step else thread.next_step,
            check_in_after=check_in_after or thread.check_in_after,
            times_offered=0,
        )
        await self._deps.threads.update(updated)
        if event or status:
            text = redact(event) if event else f"Marked {status}".replace("_", " ")
            await self._deps.threads.add_event(
                ThreadEvent(new_id(), thread.id, text, now, status, self.session.id)
            )
        return updated

    async def set_boundary(
        self,
        topic: str,
        scope: BoundaryScope = BoundaryScope.UNTIL_REOPENED,
        until: datetime | None = None,
        thread_id: str | None = None,
    ) -> Boundary:
        """Stop bringing a topic up ("aaj painting yaad nahi dilana"). Nothing is deleted.

        Allowed while saving is paused: it only holds Baatein back.
        """
        if scope is BoundaryScope.UNTIL_DATE and until is None:
            scope = BoundaryScope.UNTIL_REOPENED
        if thread_id and await self._own_thread(thread_id) is None:
            thread_id = None
        boundary = Boundary(
            id=new_id(),
            user_id=self.user_id,
            topic=redact(topic.strip()),
            created_at=self._deps.clock.now(),
            scope=scope,
            session_id=self.session.id,
            until=until if scope is BoundaryScope.UNTIL_DATE else None,
            thread_id=thread_id,
        )
        await self._deps.boundaries.add(boundary)
        return boundary

    async def lift_boundary(self, boundary_id: str) -> bool:
        """The user reopened a topic ("ab painting ke baare mein poochh sakte ho")."""
        boundary = await self._deps.boundaries.get(boundary_id)
        if boundary is None or boundary.user_id != self.user_id or boundary.lifted_at:
            return False
        await self._deps.boundaries.lift(boundary_id, self._deps.clock.now())
        return True

    async def note_declined(self, text: str) -> DeclinedSuggestion:
        """Remember a suggestion the user didn't like, so it isn't made again."""
        suggestion = DeclinedSuggestion(
            id=new_id(),
            user_id=self.user_id,
            text=redact(text),
            created_at=self._deps.clock.now(),
            session_id=self.session.id,
        )
        await self._deps.suggestions.add(suggestion)
        return suggestion

    async def set_preferences(
        self,
        *,
        address_as: str | None = None,
        pace: Pace | None = None,
        verbosity: Verbosity | None = None,
        fewer_questions: bool | None = None,
    ) -> Preferences:
        """Change how Baatein talks to the user, from now on and in later calls. Only the
        given fields change. The user asked for this, so it is saved even while saving is
        paused."""
        current = self.preferences
        self.preferences = Preferences(
            address_as=address_as.strip() if address_as else current.address_as,
            pace=pace or current.pace,
            verbosity=verbosity or current.verbosity,
            fewer_questions=(
                current.fewer_questions if fewer_questions is None else fewer_questions
            ),
        )
        await self._deps.users.set_preferences(self.user_id, self.preferences)
        return self.preferences

    async def create_reminder(
        self,
        title: str,
        *,
        on: date | None = None,
        at: time | None = None,
        repeat: Repeat = Repeat.ONCE,
        alert_days_before: tuple[int, ...] = (0,),
        about: str | None = None,
        note: str | None = None,
    ) -> Reminder:
        """Save a reminder the user asked for. Without a date it waits for one (#36).

        An explicit request, so it is saved even while saving is paused.
        """
        person = await self.person(about) if about else None
        reminder = Reminder(
            id=new_id(),
            user_id=self.user_id,
            title=redact(title),
            created_at=self._deps.clock.now(),
            on=on,
            at=at or REMINDER_TIME,
            repeat=repeat,
            alert_days_before=_alerts(alert_days_before),
            status=ReminderStatus.ACTIVE if on else ReminderStatus.NEEDS_INFO,
            note=redact(note) if note else None,
            about_person_id=person.id if person else None,
            source_session_id=self.session.id,
        )
        await self._deps.reminders.add(reminder)
        return reminder

    async def _own_open_reminder(self, reminder_id: str) -> Reminder | None:
        reminder = await self._deps.reminders.get(reminder_id)
        if reminder is None or reminder.user_id != self.user_id or not reminder.is_open:
            return None
        return reminder

    async def update_reminder(
        self,
        reminder_id: str,
        *,
        title: str | None = None,
        on: date | None = None,
        at: time | None = None,
        repeat: Repeat | None = None,
        alert_days_before: tuple[int, ...] | None = None,
        note: str | None = None,
    ) -> Reminder | None:
        """Change a reminder in place ("purani ko hi badal do", #43): never a second one."""
        reminder = await self._own_open_reminder(reminder_id)
        if reminder is None:
            return None
        on = on or reminder.on
        updated = replace(
            reminder,
            title=redact(title) if title else reminder.title,
            on=on,
            at=at or reminder.at,
            repeat=repeat or reminder.repeat,
            alert_days_before=(
                _alerts(alert_days_before) if alert_days_before else reminder.alert_days_before
            ),
            status=ReminderStatus.ACTIVE if on else ReminderStatus.NEEDS_INFO,
            note=redact(note) if note else reminder.note,
        )
        await self._deps.reminders.update(updated)
        return updated

    async def settle_occurrence(
        self, reminder_id: str, status: OccurrenceStatus, on: date | None = None
    ) -> date | None:
        """Mark one occurrence done (#42 "bill bhar diya") or skipped (#44 "sirf is baar
        skip"); the series continues. Without a date, the occurrence nearest today. A one-off
        reminder ends with it. Returns the occurrence, or None if there is none."""
        reminder = await self._own_open_reminder(reminder_id)
        if reminder is None:
            return None
        settled = await self._deps.reminders.settled(reminder_id)
        today = self._deps.clock.now().astimezone(self.timezone).date()
        day = on or nearest_open(reminder, settled, today)
        if day is None:
            return None
        now = self._deps.clock.now()
        await self._deps.reminders.settle(reminder_id, day, status, now)
        if reminder.repeat is Repeat.ONCE:
            final = (
                ReminderStatus.DONE if status is OccurrenceStatus.DONE else ReminderStatus.CANCELLED
            )
            await self._deps.reminders.update(replace(reminder, status=final))
        return day

    async def cancel_reminder(self, reminder_id: str, user_turn: int) -> ForgetOutcome:
        """Stop a reminder for good, confirmed in two steps like `forget`."""
        reminder = await self._own_open_reminder(reminder_id)
        if reminder is None:
            return ForgetOutcome.NOT_FOUND
        if not self._confirmed(reminder_id, user_turn):
            return ForgetOutcome.NEEDS_CONFIRMATION
        await self._deps.reminders.update(replace(reminder, status=ReminderStatus.CANCELLED))
        return ForgetOutcome.DELETED

    def enable_privacy_mode(self) -> None:
        """Others can hear the user ("kamre mein mehmaan hain"): no personal details aloud."""
        self.private = True

    async def close(self, reason: str | None = None) -> None:
        """Wait for pending turn writes, then end the session (ready for finalization).

        If the user was mid-sentence with no reply, the call was cut off: their last words
        become the unfinished topic, offered once next time (#31).
        """
        if self._closed:
            return
        self._closed = True
        await self.flush()
        last = self._last_turn
        abrupt = (
            last is not None
            and last.role is Role.USER
            and len(words(last.text)) >= ABRUPT_MIN_WORDS
        )
        await self._deps.sessions.end(
            self.session.id,
            self._deps.clock.now(),
            reason,
            ended_abruptly=abrupt,
            unfinished_topic=last.text[:MAX_TOPIC_CHARS] if abrupt and last else None,
            saving_paused=self.saving_paused or self.private,
        )
