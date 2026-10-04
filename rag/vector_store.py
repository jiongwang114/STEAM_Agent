from __future__ import annotations

import json
from pathlib import Path

import chromadb
from chromadb.config import Settings

from config import (
    CHROMA_PERSIST_DIR,
    EMBEDDING_MODEL,
    EMBEDDING_REVISION,
    LEGACY_MEMORY_COLLECTION_NAME,
    RERANKER_MODEL,
    RERANKER_REVISION,
)
from rag.embedder import get_embedder

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
    """Open the index; hybrid search supplies explicit BGE query vectors."""
    client = _get_client()
    return client.get_collection(
        name=current_games_collection_name(),
        embedding_function=None,
    )


def get_legacy_user_memory_collection():
    """Open the old conversation-vector collection only for user-requested cleanup."""
    client = _get_client()
    collections = client.list_collections()
    names = {
        str(getattr(collection, "name", collection))
        for collection in collections
    }
    if LEGACY_MEMORY_COLLECTION_NAME not in names:
        return None
    return client.get_collection(name=LEGACY_MEMORY_COLLECTION_NAME)


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
    expected_dimension = _embedding_dimension()
    manifest_dimension = manifest.get("embedding_dimension")
    if manifest_dimension is None:
        mismatches["embedding_dimension"] = {
            "expected": expected_dimension,
            "actual": None,
            "reason": "manifest_missing",
        }
    elif int(manifest_dimension) != expected_dimension:
        mismatches["embedding_dimension"] = {
            "expected": expected_dimension,
            "actual": manifest_dimension,
        }
    for key, expected in (
        ("embedding_revision", EMBEDDING_REVISION),
        ("reranker_revision", RERANKER_REVISION),
        ("chroma_version", chromadb.__version__),
        ("sentence_transformers_version", __import__('sentence_transformers').__version__),
    ):
        if key in manifest and manifest[key] != expected:
            mismatches[key] = {"expected": expected, "actual": manifest[key]}
    if manifest.get("game_count", 0) < 1:
        mismatches["game_count"] = {"expected": "positive", "actual": manifest.get("game_count")}
    for library, actual in manifest.get("runtime_versions", {}).items():
        expected = str(__import__(library).__version__)
        if actual != expected:
            mismatches[library] = {"expected": expected, "actual": actual}
    return {
        "status": "ok" if not mismatches else "incompatible",
        "compatible": not mismatches,
        "mismatches": mismatches,
        "index_version": manifest.get("index_version", ""),
    }


def _embedding_dimension() -> int:
    """Return the configured model dimension, rather than assuming a value."""
    dimension = get_embedder().get_sentence_embedding_dimension()
    if not dimension:
        raise RuntimeError("embedding_dimension_unavailable")
    return int(dimension)


def _chroma_embedding_wrapper(model):
    """Wrap a SentenceTransformer model into Chroma's EmbeddingFunction interface."""

    class EmbeddingFn(chromadb.EmbeddingFunction):
        def __call__(self, input: list[str]) -> list[list[float]]:
            return model.encode(input, normalize_embeddings=True).tolist()

    return EmbeddingFn()
