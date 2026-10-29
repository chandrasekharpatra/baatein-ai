from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from baatein.ports.repos import FollowUpRepo, PeopleRepo, SessionRepo, TurnRepo, UserRepo
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.migrate import migrate
from baatein.storage.sqlite.repos import (
    SqliteFollowUpRepo,
    SqlitePeopleRepo,
    SqliteSessionRepo,
    SqliteTurnRepo,
    SqliteUserRepo,
)
from tests.fakes.repos import (
    InMemoryFollowUpRepo,
    InMemoryPeopleRepo,
    InMemorySessionRepo,
    InMemoryState,
    InMemoryTurnRepo,
    InMemoryUserRepo,
)


@dataclass(frozen=True)
class Repos:
    users: UserRepo
    sessions: SessionRepo
    turns: TurnRepo
    followups: FollowUpRepo
    people: PeopleRepo


@pytest.fixture(params=["sqlite", "memory"])
async def repos(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Repos]:
    if request.param == "memory":
        state = InMemoryState()
        yield Repos(
            InMemoryUserRepo(state),
            InMemorySessionRepo(state),
            InMemoryTurnRepo(state),
            InMemoryFollowUpRepo(state),
            InMemoryPeopleRepo(state),
        )
        return

    path = tmp_path / "test.db"
    migrate(path)
    db = Database(path)
    yield Repos(
        SqliteUserRepo(db),
        SqliteSessionRepo(db),
        SqliteTurnRepo(db),
        SqliteFollowUpRepo(db),
        SqlitePeopleRepo(db),
    )
    await db.close()
