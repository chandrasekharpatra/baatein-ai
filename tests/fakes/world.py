from dataclasses import dataclass

from baatein.config import MemorySettings
from baatein.domain.models import User
from baatein.memory.service import MemoryDeps, MemoryService
from tests.factories import make_user
from tests.fakes.clock import FrozenClock
from tests.fakes.embedder import FakeEmbedder
from tests.fakes.memory import InMemoryMemoryStore
from tests.fakes.repos import (
    InMemoryBoundaryRepo,
    InMemoryPeopleRepo,
    InMemorySessionRepo,
    InMemoryState,
    InMemorySuggestionRepo,
    InMemoryThreadRepo,
    InMemoryTurnRepo,
    InMemoryUserRepo,
)


@dataclass
class World:
    state: InMemoryState
    store: InMemoryMemoryStore
    clock: FrozenClock
    embedder: FakeEmbedder
    service: MemoryService
    user: User


def build_world(settings: MemorySettings | None = None) -> World:
    state = InMemoryState()
    embedder = FakeEmbedder()
    store = InMemoryMemoryStore(state, embedder, min_similarity=0.1)
    clock = FrozenClock()
    deps = MemoryDeps(
        users=InMemoryUserRepo(state),
        sessions=InMemorySessionRepo(state),
        turns=InMemoryTurnRepo(state),
        people=InMemoryPeopleRepo(state),
        threads=InMemoryThreadRepo(state),
        boundaries=InMemoryBoundaryRepo(state),
        suggestions=InMemorySuggestionRepo(state),
        store=store,
        clock=clock,
        settings=settings or MemorySettings(),
    )
    user = make_user()
    state.users[user.id] = user
    return World(state, store, clock, embedder, MemoryService(deps, embedder), user)
