from sentence_transformers import SentenceTransformer

from ..config import EMBEDDING_MODEL

_embedder: SentenceTransformer | None = None

# BGE models use instruction prefixes to separate query vs document encoding.
# Only applied to queries — documents are embedded as-is.
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(EMBEDDING_MODEL)
    return _embedder


def embed(texts: list[str]) -> list[list[float]]:
    """Embed documents/passages (no instruction prefix)."""
    model = get_embedder()
    embeddings = model.encode(texts, normalize_embeddings=True)
    return embeddings.tolist()


def embed_query(texts: list[str]) -> list[list[float]]:
    """Embed search queries (with BGE instruction prefix when applicable)."""
    model = get_embedder()
    if _is_bge(EMBEDDING_MODEL):
        texts = [BGE_QUERY_INSTRUCTION + t for t in texts]
    embeddings = model.encode(texts, normalize_embeddings=True)
    return embeddings.tolist()


def _is_bge(model_name: str) -> bool:
    return "bge" in model_name.lower()
