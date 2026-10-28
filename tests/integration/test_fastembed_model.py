import math
from dataclasses import replace
from pathlib import Path

import pytest

from baatein.config import Settings
from baatein.memory.embeddings.registry import default_embedder_registry

pytestmark = pytest.mark.model

MODEL_CACHE = Path(__file__).resolve().parents[2] / "data" / "models"


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def test_default_model_embeds_hinglish_close_to_english() -> None:
    spec = Settings(_env_file=None).embedding.spec()  # type: ignore[call-arg]
    cached = replace(spec, options={**spec.options, "cache_dir": str(MODEL_CACHE)})
    embedder = default_embedder_registry().create(cached)

    [query] = await embedder.embed(["meri behen ki shaadi December mein hai"], "query")
    related, unrelated = await embedder.embed(
        ["User's sister is getting married in December", "User is allergic to peanuts"],
        "passage",
    )

    assert len(query) == embedder.dimensions
    assert cosine(query, related) > cosine(query, unrelated)
