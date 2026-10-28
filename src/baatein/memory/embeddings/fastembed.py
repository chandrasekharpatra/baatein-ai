import asyncio
import threading
from collections.abc import Callable, Sequence
from typing import Any

from fastembed import TextEmbedding

from baatein.ports.embedder import EmbeddingDimensionError, EmbedKind


class FastEmbedEmbedder:
    def __init__(
        self,
        model_id: str,
        dimensions: int,
        cache_dir: str | None = None,
        model_factory: Callable[..., Any] = TextEmbedding,
    ) -> None:
        self._model_id = model_id
        self._dimensions = dimensions
        self._cache_dir = cache_dir
        self._model_factory = model_factory
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str], kind: EmbedKind) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed, list(texts), kind)

    def _embed(self, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        model = self._load()
        vectors = model.query_embed(texts) if kind == "query" else model.passage_embed(texts)
        result = [[float(x) for x in vector] for vector in vectors]
        for vector in result:
            if len(vector) != self._dimensions:
                raise EmbeddingDimensionError(self._model_id, self._dimensions, len(vector))
        return result

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                self._model = self._model_factory(
                    model_name=self._model_id, cache_dir=self._cache_dir
                )
            return self._model
