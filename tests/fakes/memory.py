import math
from dataclasses import replace
from datetime import datetime

from baatein.domain.errors import NotFoundError
from baatein.domain.models import Fact, ScoredFact
from baatein.memory.ranking import reciprocal_rank_fusion
from baatein.ports.embedder import Embedder
from baatein.text import search_terms, words
from tests.fakes.repos import InMemoryState


class InMemoryMemoryStore:
    def __init__(
        self, state: InMemoryState, embedder: Embedder, min_similarity: float = 0.3
    ) -> None:
        self._state = state
        self._embedder = embedder
        self._min_similarity = min_similarity
        self._facts: dict[str, Fact] = {}
        self._vectors: dict[str, list[float]] = {}

    async def add(self, fact: Fact) -> None:
        if fact.user_id not in self._state.users:
            raise NotFoundError("user or session", fact.user_id)
        [self._vectors[fact.id]] = await self._embedder.embed([fact.text], "passage")
        self._facts[fact.id] = fact

    async def get(self, fact_id: str) -> Fact | None:
        return self._facts.get(fact_id)

    async def supersede(self, fact_id: str, replacement: Fact) -> None:
        old = self._require(fact_id)
        if not old.is_active:
            raise ValueError(f"fact '{fact_id}' is already retired")
        if old.user_id != replacement.user_id:
            raise ValueError("a fact can only be superseded by a fact of the same user")
        await self.add(replacement)
        self._facts[fact_id] = replace(
            old, superseded_by=replacement.id, retired_at=replacement.created_at
        )

    async def retire(self, fact_id: str, at: datetime) -> None:
        fact = self._require(fact_id)
        if fact.is_active:
            self._facts[fact_id] = replace(fact, retired_at=at)

    async def active(self, user_id: str, limit: int) -> list[Fact]:
        facts = [f for f in self._facts.values() if f.user_id == user_id and f.is_active]
        facts.sort(key=lambda f: (-f.importance, -f.created_at.timestamp()))
        return facts[:limit]

    async def search(self, user_id: str, query: str, limit: int) -> list[ScoredFact]:
        terms = set(search_terms(query))
        if not terms:
            return []
        [query_vector] = await self._embedder.embed([query], "query")
        facts = [f for f in self._facts.values() if f.user_id == user_id and f.is_active]

        similarity = {f.id: _cosine(query_vector, self._vectors[f.id]) for f in facts}
        semantic = sorted(
            (f.id for f in facts if similarity[f.id] >= self._min_similarity),
            key=lambda fid: -similarity[fid],
        )
        overlap = {f.id: len(terms & set(words(f.text))) for f in facts}
        keyword = sorted((f.id for f in facts if overlap[f.id]), key=lambda fid: -overlap[fid])

        scores = reciprocal_rank_fusion([semantic, keyword])
        scored = [ScoredFact(self._facts[fid], score) for fid, score in scores.items()]
        scored.sort(key=lambda s: (-s.score, -s.fact.importance, -s.fact.created_at.timestamp()))
        return scored[:limit]

    async def count_about(self, person_id: str) -> int:
        return sum(
            1 for f in self._facts.values() if f.about_person_id == person_id and f.is_active
        )

    async def forget(self, fact_id: str, at: datetime) -> list[str]:
        self._require(fact_id)
        chain: list[Fact] = []
        frontier = {fact_id}
        while frontier:
            found = [
                f
                for f in self._facts.values()
                if (f.id in frontier or f.superseded_by in frontier) and f not in chain
            ]
            chain += found
            frontier = {f.id for f in found}
        for fact in chain:
            self._facts[fact.id] = replace(
                fact,
                text="",
                quote=None,
                deleted_at=fact.deleted_at or at,
                retired_at=fact.retired_at or at,
            )
            self._vectors.pop(fact.id, None)
        return sorted({f.source_session_id for f in chain if f.source_session_id})

    def _require(self, fact_id: str) -> Fact:
        try:
            return self._facts[fact_id]
        except KeyError:
            raise NotFoundError("fact", fact_id) from None


def _cosine(a: list[float], b: list[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0
