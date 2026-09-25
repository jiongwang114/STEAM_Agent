from functools import lru_cache

from sentence_transformers import SentenceTransformer

from ..config import EMBEDDING_MODEL, MEMORY_EMBEDDING_MODEL

_embedder: SentenceTransformer | None = None
_memory_embedder: SentenceTransformer | None = None

# BGE models use instruction prefixes to separate query vs document encoding.
# Only applied to queries — documents are embedded as-is.
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(EMBEDDING_MODEL)
    return _embedder


def get_memory_embedder() -> SentenceTransformer:
    global _memory_embedder
    if _memory_embedder is None:
        _memory_embedder = SentenceTransformer(MEMORY_EMBEDDING_MODEL)
    return _memory_embedder


def embed(texts: list[str]) -> list[list[float]]:
    """Embed documents/passages (no instruction prefix)."""
    model = get_embedder()
    embeddings = model.encode(texts, normalize_embeddings=True)
    return embeddings.tolist()


def embed_memory(texts: list[str]) -> list[list[float]]:
    """Embed multilingual user conversation memories."""
    embeddings = get_memory_embedder().encode(texts, normalize_embeddings=True)
    return embeddings.tolist()


def embed_query(texts: list[str]) -> list[list[float]]:
    """Embed search queries (with BGE instruction prefix when applicable)."""
    return [list(_embed_query_one(text)) for text in texts]


@lru_cache(maxsize=1024)
def _embed_query_one(text: str) -> tuple[float, ...]:
    model = get_embedder()
    if _is_bge(EMBEDDING_MODEL):
        text = BGE_QUERY_INSTRUCTION + text
    embedding = model.encode([text], normalize_embeddings=True)[0]
    return tuple(float(value) for value in embedding)


def _is_bge(model_name: str) -> bool:
    return "bge" in model_name.lower()
