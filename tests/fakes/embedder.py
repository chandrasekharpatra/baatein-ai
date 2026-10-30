import hashlib
import math
import re
from collections.abc import Sequence

from baatein.ports.embedder import EmbedKind


def bag_of_words_vector(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    for word in re.findall(r"\w+", text.lower()):
        bucket = int.from_bytes(hashlib.sha1(word.encode()).digest()[:4], "big") % dimensions
        vector[bucket] += 1.0
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


class FakeEmbedder:
    def __init__(self, dimensions: int = 256, model_id: str = "fake/bag-of-words") -> None:
        self._dimensions = dimensions
        self._model_id = model_id
        self.calls: list[tuple[list[str], EmbedKind]] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str], kind: EmbedKind) -> list[list[float]]:
        self.calls.append((list(texts), kind))
        return [bag_of_words_vector(text, self._dimensions) for text in texts]


class MappedEmbedder(FakeEmbedder):
    """Gives chosen texts chosen vectors (normalised), so a test can set exact similarities.
    Other texts get the bag-of-words vector."""

    def __init__(self, vectors: dict[str, list[float]], dimensions: int = 256) -> None:
        super().__init__(dimensions)
        self._vectors = {
            text: [x / math.sqrt(sum(y * y for y in v)) for x in v] + [0.0] * (dimensions - len(v))
            for text, v in vectors.items()
        }

    async def embed(self, texts: Sequence[str], kind: EmbedKind) -> list[list[float]]:
        self.calls.append((list(texts), kind))
        return [
            self._vectors.get(text) or bag_of_words_vector(text, self._dimensions) for text in texts
        ]
