from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

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
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.migrate import migrate
from baatein.storage.sqlite.repos import (
    SqliteBoundaryRepo,
    SqlitePeopleRepo,
    SqliteReminderRepo,
    SqliteSessionRepo,
    SqliteSuggestionRepo,
    SqliteThreadRepo,
    SqliteTurnRepo,
    SqliteUserRepo,
)
from tests.fakes.repos import (
    InMemoryBoundaryRepo,
    InMemoryPeopleRepo,
    InMemoryReminderRepo,
    InMemorySessionRepo,
    InMemoryState,
    InMemorySuggestionRepo,
    InMemoryThreadRepo,
    InMemoryTurnRepo,
    InMemoryUserRepo,
)


@dataclass(frozen=True)
class Repos:
    users: UserRepo
    sessions: SessionRepo
    turns: TurnRepo
    people: PeopleRepo
    threads: ThreadRepo
    boundaries: BoundaryRepo
    suggestions: SuggestionRepo
    reminders: ReminderRepo


@pytest.fixture(params=["sqlite", "memory"])
async def repos(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Repos]:
    if request.param == "memory":
        state = InMemoryState()
        yield Repos(
            InMemoryUserRepo(state),
            InMemorySessionRepo(state),
            InMemoryTurnRepo(state),
            InMemoryPeopleRepo(state),
            InMemoryThreadRepo(state),
            InMemoryBoundaryRepo(state),
            InMemorySuggestionRepo(state),
            InMemoryReminderRepo(state),
        )
        return

    path = tmp_path / "test.db"
    migrate(path)
    db = Database(path)
    yield Repos(
        SqliteUserRepo(db),
        SqliteSessionRepo(db),
        SqliteTurnRepo(db),
        SqlitePeopleRepo(db),
        SqliteThreadRepo(db),
        SqliteBoundaryRepo(db),
        SqliteSuggestionRepo(db),
        SqliteReminderRepo(db),
    )
    await db.close()
