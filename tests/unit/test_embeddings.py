from collections.abc import Iterable
from typing import Any, ClassVar

import pytest

from baatein.config import Settings
from baatein.memory.embeddings.fastembed import FastEmbedEmbedder
from baatein.memory.embeddings.registry import default_embedder_registry
from baatein.ports.embedder import EmbeddingDimensionError


class StubModel:
    instances: ClassVar[list["StubModel"]] = []

    def __init__(self, model_name: str, cache_dir: str | None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.calls: list[tuple[str, list[str]]] = []
        StubModel.instances.append(self)

    def query_embed(self, texts: Iterable[str]) -> Iterable[list[float]]:
        texts = list(texts)
        self.calls.append(("query", texts))
        return [[1.0, 0.0, 0.0] for _ in texts]

    def passage_embed(self, texts: Iterable[str]) -> Iterable[list[float]]:
        texts = list(texts)
        self.calls.append(("passage", texts))
        return [[0.0, 1.0, float(len(t))] for t in texts]


@pytest.fixture(autouse=True)
def reset_stub() -> None:
    StubModel.instances.clear()


def embedder(dimensions: int = 3) -> FastEmbedEmbedder:
    return FastEmbedEmbedder("org/model", dimensions, cache_dir="cache", model_factory=StubModel)


async def test_routes_kind_to_query_or_passage_embedding() -> None:
    e = embedder()

    queries = await e.embed(["q"], "query")
    passages = await e.embed(["ab", "abc"], "passage")

    assert queries == [[1.0, 0.0, 0.0]]
    assert passages == [[0.0, 1.0, 2.0], [0.0, 1.0, 3.0]]
    assert StubModel.instances[0].calls == [("query", ["q"]), ("passage", ["ab", "abc"])]


async def test_loads_model_lazily_once_with_settings() -> None:
    e = embedder()
    assert StubModel.instances == []

    await e.embed(["a"], "query")
    await e.embed(["b"], "query")

    [model] = StubModel.instances
    assert (model.model_name, model.cache_dir) == ("org/model", "cache")
    assert (e.model_id, e.dimensions) == ("org/model", 3)


async def test_empty_input_skips_model() -> None:
    assert await embedder().embed([], "passage") == []
    assert StubModel.instances == []


async def test_dimension_mismatch_raises() -> None:
    with pytest.raises(EmbeddingDimensionError, match="returned 3-dim vectors, expected 4"):
        await embedder(dimensions=4).embed(["a"], "query")


def test_default_registry_builds_fastembed_from_settings() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    built: Any = default_embedder_registry().create(settings.embedding.spec())

    assert isinstance(built, FastEmbedEmbedder)
    assert built.model_id == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert built.dimensions == 384
