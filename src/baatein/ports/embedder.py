from collections.abc import Sequence
from typing import Literal, Protocol

EmbedKind = Literal["passage", "query"]


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: Sequence[str], kind: EmbedKind) -> list[list[float]]: ...


class EmbeddingDimensionError(ValueError):
    def __init__(self, model_id: str, expected: int, actual: int) -> None:
        super().__init__(f"{model_id} returned {actual}-dim vectors, expected {expected}")
