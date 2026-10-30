import asyncio
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from baatein.config import Settings
from baatein.memory.embeddings.fastembed import FastEmbedEmbedder
from baatein.memory.embeddings.pinecone import PineconeEmbedder
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


def test_default_registry_builds_pinecone_llama_from_settings() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    built: Any = default_embedder_registry().create(settings.embedding.spec())

    assert isinstance(built, PineconeEmbedder)
    assert (built.model_id, built.dimensions) == ("llama-text-embed-v2", 2048)
    assert "cache_dir" not in settings.embedding.spec().options


def test_registry_still_builds_a_local_fastembed_model() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        embedding={
            "provider": "fastembed",
            "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "dimensions": 384,
        },
    )

    built: Any = default_embedder_registry().create(settings.embedding.spec())

    assert isinstance(built, FastEmbedEmbedder)
    assert settings.embedding.spec().options["cache_dir"] == "data/models"


class FakeInference:
    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions
        self.calls: list[dict[str, Any]] = []

    async def embed(self, **request: Any) -> list[SimpleNamespace]:
        self.calls.append(request)
        return [SimpleNamespace(values=[0.5] * self.dimensions) for _ in request["inputs"]]


class FakePinecone:
    made: ClassVar[list["FakePinecone"]] = []

    def __init__(self, api_key: str | None, dimensions: int = 4) -> None:
        self.api_key = api_key
        self.inference = FakeInference(dimensions)
        FakePinecone.made.append(self)


@pytest.fixture(autouse=True)
def reset_pinecone() -> None:
    FakePinecone.made.clear()


async def test_pinecone_sends_kind_dimension_and_batches() -> None:
    embedder = PineconeEmbedder("llama-text-embed-v2", 4, "key", client_factory=FakePinecone)

    vectors = await embedder.embed([f"memory {n}" for n in range(95)], "passage")
    [query] = await embedder.embed(["father"], "query")

    assert len(vectors) == 95
    assert query == [0.5] * 4
    [client] = FakePinecone.made  # one client for the loop
    first, second, third = client.inference.calls
    assert [len(call["inputs"]) for call in (first, second)] == [90, 5]
    assert first["model"] == "llama-text-embed-v2"
    assert first["parameters"] == {"input_type": "passage", "truncate": "END", "dimension": 4}
    assert third["parameters"]["input_type"] == "query"
    assert client.api_key == "key"
    assert await embedder.embed([], "query") == []


async def test_pinecone_rejects_vectors_of_the_wrong_size() -> None:
    def factory(api_key: str | None) -> FakePinecone:
        return FakePinecone(api_key, dimensions=3)

    embedder = PineconeEmbedder("llama-text-embed-v2", 4, client_factory=factory)

    with pytest.raises(EmbeddingDimensionError):
        await embedder.embed(["x"], "query")


def test_pinecone_makes_a_client_per_event_loop() -> None:
    embedder = PineconeEmbedder("llama-text-embed-v2", 4, client_factory=FakePinecone)

    for _ in range(2):
        asyncio.run(embedder.embed(["x"], "query"))

    assert len(FakePinecone.made) == 2
