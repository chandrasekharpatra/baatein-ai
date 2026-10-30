from collections.abc import Hashable, Sequence

RRF_K = 60

KEYWORD_FLOOR = 0.1
"""A keyword match is kept only if it is at least this close in meaning to the query. Shared
words alone pulled up unrelated memories ("sleep", "pine cone": similarity 0.00-0.08), while
every keyword match that was the right answer scored 0.13 or more (2026-10-30 evaluation)."""


def reciprocal_rank_fusion[K: Hashable](
    rankings: Sequence[Sequence[K]], k: int = RRF_K
) -> dict[K, float]:
    scores: dict[K, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return scores
