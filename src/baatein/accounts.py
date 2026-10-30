"""Test accounts for admins: load one from a seed file, list them, download one as a seed file.

A download loads back as the same account: download, upload with "replace", download again,
and the two files match. Dates in a download are relative to the moment it was made ("today+2",
`days_ago`), so a scenario stays as fresh when loaded on a later day; anything saved in a call
is placed five minutes into that call, and turns 40 seconds apart (see `memory/seed.py`).
"""

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime

from baatein.config import Settings
from baatein.domain.errors import BaateinError
from baatein.domain.ids import new_id
from baatein.domain.models import (
    Boundary,
    BoundaryScope,
    Certainty,
    Fact,
    Preferences,
    ReminderStatus,
    Session,
    Thread,
    User,
)
from baatein.memory.seed import (
    SeedBoundary,
    SeedCall,
    SeedDeclined,
    Seeder,
    SeedError,
    SeedEvent,
    SeedFile,
    SeedMemory,
    SeedOccurrence,
    SeedPerson,
    SeedPreferences,
    SeedReminder,
    SeedResult,
    SeedThread,
    SeedTurn,
    SeedUser,
)
from baatein.memory.service import MemoryDeps, zone
from baatein.ports.auth import PasswordHasher
from baatein.storage.sqlite.accounts import (
    AccountData,
    AccountRow,
    list_accounts,
    read_account,
    take_over,
)
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.repos import delete_user
from baatein.voice.languages import LanguageRegistry


class AccountExistsError(BaateinError):
    def __init__(self, email: str) -> None:
        super().__init__(f"{email} already exists; choose replace to load it again")
        self.email = email


@dataclass(frozen=True)
class Loaded:
    user: User
    added: SeedResult
    replaced: bool


class Accounts:
    def __init__(
        self,
        db: Database,
        deps: MemoryDeps,
        seeder: Seeder,
        hasher: PasswordHasher,
        languages: LanguageRegistry,
        settings: Settings,
    ) -> None:
        self._db = db
        self._deps = deps
        self._seeder = seeder
        self._hasher = hasher
        self._languages = languages
        self._settings = settings

    async def exists(self, email: str) -> bool:
        return await self._deps.users.get_by_email(email.strip().lower()) is not None

    async def load(
        self, seed: SeedFile, *, replace: bool = False, password: str | None = None
    ) -> Loaded:
        """Create the account in the file's `user` section, or with `replace`, load it again
        from scratch. The old account stays until the new one has loaded completely."""
        if seed.user is None:
            raise SeedError("the file needs a 'user' section naming the account")
        email = seed.user.email.strip().lower()
        if self._settings.auth.is_admin(email):
            raise SeedError(f"{email} is an admin; admin accounts aren't loaded from files")
        self._languages.get(seed.user.language)
        existing = await self._deps.users.get_by_email(email)
        if existing is not None and not replace:
            raise AccountExistsError(email)
        password = password or seed.user.password
        if password is not None:
            if len(password) < self._settings.auth.min_password_length:
                raise SeedError(
                    f"password must be at least {self._settings.auth.min_password_length}"
                    " characters"
                )
            password_hash = self._hasher.hash(password)
        elif existing is not None:
            password_hash = existing.password_hash
        else:
            raise SeedError(f"{email} is a new account, so it needs a password")
        loading = seed.model_copy(
            update={"user": seed.user.model_copy(update={"email": f"{new_id()}@loading.invalid"})}
        )
        user = await self._seeder.create_user(loading, password_hash)
        try:
            added = await self._seeder.load(user, loading)
        except Exception:
            await delete_user(self._db, user.email)
            raise
        replaced = await take_over(self._db, user.id, email)
        return Loaded(dataclasses.replace(user, email=email), added, replaced)

    async def list(self) -> list[AccountRow]:
        return await list_accounts(self._db)

    def is_admin(self, user: User) -> bool:
        return self._settings.auth.is_admin(user.email)

    async def export(self, user_id: str) -> SeedFile | None:
        account = await read_account(self._db, user_id)
        return to_seed(account, self._deps.clock.now()) if account else None


def to_seed(account: AccountData, now: datetime) -> SeedFile:
    """An account as a seed file that loads back as the same account."""
    return _Export(account, now).seed()


class _Export:
    def __init__(self, account: AccountData, now: datetime) -> None:
        self._account = account
        self._tz = zone(account.user.timezone)
        self._today = now.astimezone(self._tz).date()
        self._names = {person.id: person.name for person in account.people}
        self._facts = self._kept_facts()
        self._sessions = self._kept_calls()
        self._calls = {s.id: f"c{n}" for n, s in enumerate(self._sessions, start=1)}
        bounded = {b.thread_id for b in self._boundaries()}
        self._thread_keys = {
            thread.id: f"t{n}"
            for n, thread in enumerate((t for t in account.threads if t.id in bounded), start=1)
        }

    def seed(self) -> SeedFile:
        user = self._account.user
        return SeedFile(
            user=SeedUser(
                email=user.email,
                display_name=user.display_name,
                language=user.language,
                timezone=user.timezone,
            ),
            persona=user.persona,
            preferences=(
                SeedPreferences(**vars(user.preferences))
                if user.preferences != Preferences()
                else None
            ),
            people=[SeedPerson(name=p.name, relation=p.relation) for p in self._account.people],
            calls=[self._call(session) for session in self._sessions],
            memories=self._memories(),
            threads=[self._thread(thread) for thread in self._account.threads],
            boundaries=[
                SeedBoundary(
                    topic=b.topic,
                    scope="until_date" if b.scope is BoundaryScope.UNTIL_DATE else "until_reopened",
                    until=self._day(b.until) if b.until else None,
                    thread=self._thread_keys.get(b.thread_id) if b.thread_id else None,
                    call=self._call_key(b.session_id),
                )
                for b in self._boundaries()
            ],
            declined_suggestions=[
                SeedDeclined(text=d.text, call=key)
                if (key := self._call_key(d.session_id))
                else d.text
                for d in self._account.declined
            ],
            reminders=[
                SeedReminder(
                    title=r.title,
                    date=self._relative(r.on) if r.on else None,
                    time=r.at.strftime("%H:%M"),
                    repeat=r.repeat,
                    days_before=list(r.alert_days_before),
                    about=self._about(r.about_person_id),
                    note=r.note,
                    status=(
                        r.status.value
                        if r.status in (ReminderStatus.DONE, ReminderStatus.CANCELLED)
                        else None
                    ),
                    settled=[
                        SeedOccurrence(date=self._relative(on), status=status)
                        for on, status in self._account.settled.get(r.id, {}).items()
                    ],
                    call=self._call_key(r.source_session_id),
                )
                for r in self._account.reminders
            ],
        )

    def _relative(self, day: date) -> str:
        offset = (day - self._today).days
        return "today" if offset == 0 else f"today{offset:+d}"

    def _day(self, moment: datetime) -> str:
        return self._relative(moment.astimezone(self._tz).date())

    def _days_ago(self, moment: datetime) -> int:
        return max(0, (self._today - moment.astimezone(self._tz).date()).days)

    def _about(self, person_id: str | None) -> str | None:
        return self._names.get(person_id) if person_id else None

    def _kept_facts(self) -> list[Fact]:
        """Memories not forgotten, with the earlier versions of corrected ones."""
        by_id = {fact.id: fact for fact in self._account.facts if fact.deleted_at is None}

        def kept(fact: Fact) -> bool:
            if fact.retired_at is None:
                return True
            later = by_id.get(fact.superseded_by) if fact.superseded_by else None
            return later is not None and kept(later)

        return [fact for fact in by_id.values() if kept(fact)]

    def _boundaries(self) -> list[Boundary]:
        return [
            b
            for b in self._account.boundaries
            if b.lifted_at is None and b.scope is not BoundaryScope.THIS_CALL
        ]

    def _kept_calls(self) -> list[Session]:
        """Calls something was said or saved in, oldest first, keyed c1, c2, …"""
        account = self._account
        linked = {
            *(f.source_session_id for f in self._facts),
            *(t.source_session_id for t in account.threads),
            *(e.session_id for events in account.events.values() for e in events),
            *(b.session_id for b in self._boundaries()),
            *(d.session_id for d in account.declined),
            *(r.source_session_id for r in account.reminders),
        }
        return [
            s for s in account.sessions if s.summary or account.turns.get(s.id) or s.id in linked
        ]

    def _call_key(self, session_id: str | None) -> str | None:
        return self._calls.get(session_id) if session_id else None

    def _call(self, session: Session) -> SeedCall:
        local = session.started_at.astimezone(self._tz)
        seconds = (session.ended_at - session.started_at).total_seconds() if session.ended_at else 0
        return SeedCall(
            key=self._calls[session.id],
            days_ago=self._days_ago(session.started_at),
            at=local.strftime("%H:%M"),
            minutes=max(1, round(seconds / 60)),
            summary=session.summary,
            ended_abruptly=session.ended_abruptly,
            unfinished_topic=session.unfinished_topic,
            notes=list(session.notes),
            turns=[
                SeedTurn(role=turn.role, text=turn.text)
                for turn in self._account.turns.get(session.id, [])
            ],
        )

    def _memories(self) -> list[SeedMemory]:
        """Each corrected memory comes just before the one that corrects it."""
        corrected_by = {f.superseded_by: f for f in self._facts if f.superseded_by}
        keys: dict[str, str] = {}
        ordered: dict[str, Fact] = {}

        def add(fact: Fact) -> None:
            if fact.id in ordered:
                return
            if (earlier := corrected_by.get(fact.id)) is not None:
                add(earlier)
            ordered[fact.id] = fact

        for fact in self._facts:
            add(fact)
        memories = []
        for fact in ordered.values():
            if fact.superseded_by:
                keys[fact.id] = f"m{len(keys) + 1}"
            earlier = corrected_by.get(fact.id)
            memories.append(
                SeedMemory(
                    key=keys.get(fact.id),
                    corrects=keys.get(earlier.id) if earlier else None,
                    text=fact.text,
                    kind=fact.kind,
                    quote=fact.quote,
                    about=self._about(fact.about_person_id),
                    sensitivity=fact.sensitivity,
                    uncertain=fact.certainty is Certainty.UNCERTAIN,
                    importance=fact.importance,
                    call=self._call_key(fact.source_session_id),
                )
            )
        return memories

    def _thread(self, thread: Thread) -> SeedThread:
        offered = thread.last_offered_at
        offered_days = self._days_ago(offered) if offered else None
        return SeedThread(
            key=self._thread_keys.get(thread.id),
            title=thread.title,
            kind=thread.kind,
            status=thread.status,
            next_step=thread.next_step,
            check_in_after=self._day(thread.check_in_after) if thread.check_in_after else None,
            last_offered_days_ago=offered_days,
            times_offered=(
                thread.times_offered
                if thread.times_offered != int(offered_days is not None)
                else None
            ),
            about=self._about(thread.about_person_id),
            sensitivity=thread.sensitivity,
            call=self._call_key(thread.source_session_id),
            events=[
                SeedEvent(text=e.text, status=e.status, call=key)
                if (key := self._call_key(e.session_id))
                else SeedEvent(text=e.text, status=e.status, days_ago=self._days_ago(e.created_at))
                for e in self._account.events.get(thread.id, [])
            ],
        )
