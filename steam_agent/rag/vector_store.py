from __future__ import annotations

import json
from pathlib import Path

import chromadb
from chromadb.config import Settings

from ..config import CHROMA_PERSIST_DIR, EMBEDDING_MODEL, MEMORY_COLLECTION_NAME, RERANKER_MODEL
from .embedder import get_embedder, get_memory_embedder

_client: chromadb.PersistentClient | None = None
_DATA_DIR = Path(CHROMA_PERSIST_DIR)
_CURRENT_INDEX_PATH = _DATA_DIR / "current_index.json"


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
        name=current_games_collection_name(),
        embedding_function=_chroma_embedding_wrapper(embedder),
        metadata={"hnsw:space": "cosine"},
    )


def get_user_memory_collection():
    """Get or create the user memory collection."""
    client = _get_client()
    embedder = get_memory_embedder()
    return client.get_or_create_collection(
        name=MEMORY_COLLECTION_NAME,
        embedding_function=_chroma_embedding_wrapper(embedder),
        metadata={"hnsw:space": "cosine"},
    )


def current_games_collection_name() -> str:
    if _CURRENT_INDEX_PATH.exists():
        try:
            value = json.loads(_CURRENT_INDEX_PATH.read_text(encoding="utf-8"))
            name = str(value.get("collection", "")).strip()
            if name:
                return name
        except (OSError, ValueError):
            pass
    return "games"


def index_manifest() -> dict:
    path = _DATA_DIR / "index_manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def index_compatibility() -> dict:
    manifest = index_manifest()
    if not manifest:
        return {"status": "missing", "compatible": False, "reason": "manifest_missing"}
    mismatches = {}
    if manifest.get("embedding_model") != EMBEDDING_MODEL:
        mismatches["embedding_model"] = {
            "expected": EMBEDDING_MODEL,
            "actual": manifest.get("embedding_model"),
        }
    if manifest.get("reranker_model") != RERANKER_MODEL:
        mismatches["reranker_model"] = {
            "expected": RERANKER_MODEL,
            "actual": manifest.get("reranker_model"),
        }
    return {
        "status": "ok" if not mismatches else "incompatible",
        "compatible": not mismatches,
        "mismatches": mismatches,
        "index_version": manifest.get("index_version", ""),
    }


def reset_user_memory_collection():
    client = _get_client()
    try:
        client.delete_collection(MEMORY_COLLECTION_NAME)
    except Exception:
        pass
    return get_user_memory_collection()


def _chroma_embedding_wrapper(model):
    """Wrap a SentenceTransformer model into Chroma's EmbeddingFunction interface."""

    class EmbeddingFn(chromadb.EmbeddingFunction):
        def __call__(self, input: list[str]) -> list[list[float]]:
            return model.encode(input, normalize_embeddings=True).tolist()

    return EmbeddingFn()
