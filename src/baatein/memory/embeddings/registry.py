from baatein.memory.embeddings.fastembed import FastEmbedEmbedder
from baatein.memory.embeddings.pinecone import PineconeEmbedder
from baatein.ports.embedder import Embedder
from baatein.registry import ProviderRegistry


class EmbedderRegistry(ProviderRegistry[Embedder]):
    kind = "embedding"


def default_embedder_registry() -> EmbedderRegistry:
    return EmbedderRegistry({"fastembed": FastEmbedEmbedder, "pinecone": PineconeEmbedder})
