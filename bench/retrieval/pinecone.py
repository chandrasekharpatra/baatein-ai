"""Pinecone's hosted inference (embed and rerank), for the retrieval benchmark.

Model names in the benchmark look like `pinecone/llama-text-embed-v2:1024` (embedding, with
its output dimension) and `pinecone/cohere-rerank-3.5` (reranker). Needs PINECONE_API_KEY.
Timings include the network round trip, as a call would see it.
"""

import asyncio
import os
from collections.abc import Sequence
from typing import Any

import httpx

API = "https://api.pinecone.io"
HEADERS = {"X-Pinecone-API-Version": "2025-04"}
PREFIX = "pinecone/"
BATCH = 90  # under the embed endpoint's 96-input limit


def is_hosted(model: str) -> bool:
    return model.startswith(PREFIX)


def parse(model: str) -> tuple[str, int | None]:
    """`pinecone/llama-text-embed-v2:1024` -> ("llama-text-embed-v2", 1024)."""
    name, _, dim = model.removeprefix(PREFIX).partition(":")
    return name, int(dim) if dim else None


class Client:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=API,
            headers={**HEADERS, "Api-Key": os.environ["PINECONE_API_KEY"]},
            timeout=30,
        )

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(5):
            response = await self._http.post(path, json=body)
            if response.status_code != 429:
                response.raise_for_status()
                result: dict[str, Any] = response.json()
                return result
            await asyncio.sleep(2**attempt)  # rate limited: back off
        response.raise_for_status()
        raise AssertionError("unreachable")


class PineconeEmbedder:
    def __init__(self, model: str, client: Client) -> None:
        self._name, self.dimensions = parse(model)
        self._client = client

    async def embed(self, texts: Sequence[str], kind: str) -> list[list[float]]:
        parameters: dict[str, Any] = {"input_type": kind, "truncate": "END"}
        if self.dimensions:
            parameters["dimension"] = self.dimensions
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            body = {
                "model": self._name,
                "parameters": parameters,
                "inputs": [{"text": text} for text in texts[start : start + BATCH]],
            }
            data = await self._client.post("/embed", body)
            vectors += [item["values"] for item in data["data"]]
        return vectors


class PineconeReranker:
    def __init__(self, model: str, client: Client) -> None:
        self._name, _ = parse(model)
        self._client = client

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        body = {
            "model": self._name,
            "query": query,
            "documents": [{"text": text} for text in documents],
            "top_n": len(documents),
            "return_documents": False,
            "parameters": {"truncate": "END"},
        }
        data = await self._client.post("/rerank", body)
        scores = [0.0] * len(documents)
        for item in data["data"]:
            scores[item["index"]] = float(item["score"])
        return scores
