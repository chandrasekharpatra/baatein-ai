from datetime import datetime
from typing import Protocol

from baatein.domain.models import Fact, ScoredFact


class MemoryStore(Protocol):
    async def add(self, fact: Fact) -> None: ...
    async def add_unless_similar(self, fact: Fact, min_similarity: float) -> list[ScoredFact]:
        """Add the fact unless current facts of the same user are at least this similar to it.
        Returns those instead (most similar first, scored by similarity), without saving."""
        ...

    async def get(self, fact_id: str) -> Fact | None: ...
    async def supersede(self, fact_id: str, replacement: Fact) -> None: ...
    async def retire(self, fact_id: str, at: datetime) -> None: ...
    async def active(self, user_id: str, limit: int) -> list[Fact]: ...
    async def search(
        self, user_id: str, query: str, limit: int, min_similarity: float | None = None
    ) -> list[ScoredFact]:
        """Hybrid search. `min_similarity` overrides the store's cut-off for meaning matches
        (it depends on the language of `query`)."""
        ...

    async def saved_in(self, session_id: str) -> list[Fact]:
        """Current facts the user agreed to keep in this call, oldest first."""
        ...

    async def count_about(self, person_id: str) -> int:
        """How many current (not retired or deleted) facts are about this person."""
        ...

    async def forget_all(self, user_id: str, at: datetime) -> None:
        """Tombstone every fact of the user, as `forget` does one ("Forget me")."""
        ...

    async def forget(self, fact_id: str, at: datetime) -> list[str]:
        """Tombstone the fact and every earlier version it replaced: wipe the text, quote and
        vectors, keep the row with `deleted_at`. Returns the ids of the sessions they came from."""
        ...
