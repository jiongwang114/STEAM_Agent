from __future__ import annotations

import chromadb
from chromadb.config import Settings

from ..config import CHROMA_PERSIST_DIR
from .embedder import get_embedder

_client: chromadb.PersistentClient | None = None


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=CHROMA_PERSIST_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def get_games_collection():
    """Get or create the games knowledge base collection."""
    client = _get_client()
    embedder = get_embedder()
    return client.get_or_create_collection(
        name="games",
        embedding_function=_chroma_embedding_wrapper(embedder),
        metadata={"hnsw:space": "cosine"},
    )


def get_user_memory_collection():
    """Get or create the user memory collection."""
    client = _get_client()
    embedder = get_embedder()
    return client.get_or_create_collection(
        name="user_memory",
        embedding_function=_chroma_embedding_wrapper(embedder),
        metadata={"hnsw:space": "cosine"},
    )


def _chroma_embedding_wrapper(model):
    """Wrap a SentenceTransformer model into Chroma's EmbeddingFunction interface."""

    class EmbeddingFn(chromadb.EmbeddingFunction):
        def __call__(self, input: list[str]) -> list[list[float]]:
            from .embedder import embed
            return embed(input)

    return EmbeddingFn()
