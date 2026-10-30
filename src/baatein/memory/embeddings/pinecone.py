"""Embeddings from Pinecone's hosted inference (llama-text-embed-v2 by default).

Chosen on 2026-10-30 from docs/RETRIEVAL_BENCHMARK.md: the best model for English, Hindi and
Roman Hinglish there, at the cost of a network round trip (about 400 ms from India), which
per-turn recall hides by starting on the final transcript (see `Assistant`).
"""

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

from pinecone import PineconeAsyncio

from baatein.ports.embedder import EmbeddingDimensionError, EmbedKind

BATCH = 90
"""Inputs per request; the embed endpoint takes at most 96."""


class PineconeEmbedder:
    def __init__(
        self,
        model_id: str,
        dimensions: int,
        api_key: str | None = None,
        client_factory: Callable[..., Any] = PineconeAsyncio,
    ) -> None:
        self._model_id = model_id
        self._dimensions = dimensions
        self._api_key = api_key  # None: the SDK reads PINECONE_API_KEY
        self._client_factory = client_factory
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str], kind: EmbedKind) -> list[list[float]]:
        if not texts:
            return []
        client = self._connect()
        parameters = {"input_type": kind, "truncate": "END", "dimension": self._dimensions}
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            batch = list(texts[start : start + BATCH])
            result = await client.inference.embed(
                model=self._model_id, inputs=batch, parameters=parameters
            )
            vectors += [[float(x) for x in embedding.values] for embedding in result]
        for vector in vectors:
            if len(vector) != self._dimensions:
                raise EmbeddingDimensionError(self._model_id, self._dimensions, len(vector))
        return vectors

    def _connect(self) -> Any:
        """One client per event loop: its connection pool belongs to the loop that made it."""
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:
            self._client = self._client_factory(api_key=self._api_key)
            self._loop = loop
        return self._client
