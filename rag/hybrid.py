from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from config import (
    CHROMA_PERSIST_DIR,
    RAG_DENSE_CANDIDATES,
    RAG_LEXICAL_CANDIDATES,
    RAG_RERANK_CANDIDATES,
    RAG_RERANK_WEIGHT,
)
from rag.embedder import embed_query
from rag.fusion import reciprocal_rank_fusion
from rag.ingest import build_chunk
from rag.lexical import BM25Index, LexicalDocument
from rag.reranker import rerank
from rag.vector_store import current_games_collection_name, get_games_collection


CACHE_PATH = Path(CHROMA_PERSIST_DIR) / "game_cache.json"
_index: BM25Index | None = None
_documents: dict[str, LexicalDocument] = {}
_index_collection_name = ""
_lock = threading.Lock()


def reset_retrieval_cache() -> None:
    """Reset process-local lexical state between independent evaluations."""
    global _index, _documents, _index_collection_name
    with _lock:
        _index = None
        _documents = {}
        _index_collection_name = ""


def _cache_signature() -> tuple[int, int]:
    try:
        path = CACHE_PATH
        pointer = Path(CHROMA_PERSIST_DIR) / "current_index.json"
        if pointer.exists():
            value = json.loads(pointer.read_text(encoding="utf-8"))
            path = Path(CHROMA_PERSIST_DIR) / str(value.get("cache", path.name))
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return (0, 0)


def hybrid_search(
    query: str,
    *,
    top_k: int,
    where: dict | None = None,
    use_dense: bool = True,
    use_lexical: bool = True,
    use_reranker: bool = True,
    reranker_weight: float = RAG_RERANK_WEIGHT,
    required_genre: str | None = None,
) -> dict:
    timings = {}
    started = time.perf_counter()
    index, documents = _get_lexical_index()
    # Some evaluation tags are Steam user tags and are not present in every
    # cached record. Only enforce a tag filter when the current index can
    # actually represent it; otherwise retain semantic retrieval.
    if required_genre and not any(
        _genre_matches(document.metadata, required_genre)
        for document in documents.values()
    ):
        return {"results": [], "retrieval": {"status": "unsupported_filter", "unsupported_filter": "genre"}}
    timings["index_load_ms"] = _ms(started)

    dense_started = time.perf_counter()
    dense_error = ""
    # Chroma can only filter metadata that was present when the frozen index
    # was built. New boolean fields are enforced again after retrieval using
    # the cache-backed lexical metadata, so a stale index cannot silently
    # ignore a hard constraint.
    chroma_where, post_filter = _split_where(where)
    if use_dense:
        try:
            collection = get_games_collection()
            dense_raw = collection.query(
                query_embeddings=embed_query([query]),
                n_results=RAG_DENSE_CANDIDATES * 3,
                **({"where": chroma_where} if chroma_where else {}),
            )
        except Exception as exc:
            dense_raw = {"ids": [[]], "distances": [[]]}
            dense_error = type(exc).__name__
    else:
        dense_raw = {"ids": [[]], "distances": [[]]}
    chunk_ids = list(dense_raw.get("ids", [[]])[0])
    dense_distances = list(dense_raw.get("distances", [[]])[0])
    dense_ids = []
    dense_scores = {}
    for position, chunk_id in enumerate(chunk_ids):
        doc_id = chunk_id.split(":", 1)[0]
        if doc_id not in dense_scores:
            dense_ids.append(doc_id)
            dense_scores[doc_id] = 1 - float(dense_distances[position])
        if len(dense_ids) >= RAG_DENSE_CANDIDATES:
            break
    timings["dense_ms"] = _ms(dense_started)

    if dense_error and use_dense:
        timings["total_ms"] = _ms(started)
        return {
            "results": [],
            "retrieval": {
                "status": "degraded",
                "dense_error": dense_error,
                "reranker_used": False,
                "mode": {"dense": use_dense, "lexical": use_lexical, "reranker": use_reranker},
                "timings": timings,
            },
        }

    lexical_started = time.perf_counter()
    predicate = _metadata_predicate(where, required_genre=required_genre)
    lexical_rows = (
        index.search(query, RAG_LEXICAL_CANDIDATES, predicate=predicate)
        if use_lexical else []
    )
    lexical_ids = [document.doc_id for document, _ in lexical_rows]
    lexical_scores = {document.doc_id: score for document, score in lexical_rows}
    timings["lexical_ms"] = _ms(lexical_started)

    fusion_started = time.perf_counter()
    rankings = [ranking for ranking in (dense_ids, lexical_ids) if ranking]
    fused = reciprocal_rank_fusion(rankings)
    # Apply hard filters before truncating the fused ranking so matching games
    # cannot be pushed out by unrelated high-scoring candidates.
    candidates = [
        doc_id for doc_id, _ in fused
        if doc_id in documents
        and _genre_matches(documents[doc_id].metadata, required_genre)
        and (not post_filter or _matches(documents[doc_id].metadata, post_filter))
    ][:RAG_RERANK_CANDIDATES]
    fusion_scores = dict(fused)
    timings["fusion_ms"] = _ms(fusion_started)

    rerank_started = time.perf_counter()
    candidate_docs = [
        documents[doc_id] for doc_id in candidates
        if doc_id in documents
        and _genre_matches(documents[doc_id].metadata, required_genre)
        and (not post_filter or _matches(documents[doc_id].metadata, post_filter))
    ]
    if use_reranker:
        rerank_scores, reranker_used = rerank(
            query, [document.text for document in candidate_docs]
        )
    else:
        rerank_scores, reranker_used = [], False
    rows = []
    normalized_rerank = _minmax(rerank_scores) if reranker_used else []
    normalized_fusion = _minmax([
        fusion_scores.get(document.doc_id, 0.0) for document in candidate_docs
    ])
    for index_value, document in enumerate(candidate_docs):
        final_score = (
            reranker_weight * normalized_rerank[index_value]
            + (1 - reranker_weight) * normalized_fusion[index_value]
            if reranker_used else normalized_fusion[index_value]
        )
        rows.append({
            "appid": document.doc_id,
            "document": document.text,
            "metadata": document.metadata,
            "dense_similarity": dense_scores.get(document.doc_id),
            "lexical_score": lexical_scores.get(document.doc_id, 0.0),
            "fusion_score": fusion_scores.get(document.doc_id, 0.0),
            "rerank_score": rerank_scores[index_value] if reranker_used else None,
            "final_score": final_score,
        })
    if reranker_used:
        rows.sort(key=lambda row: row["final_score"], reverse=True)
    else:
        rows.sort(key=lambda row: row["fusion_score"], reverse=True)
    timings["rerank_ms"] = _ms(rerank_started)
    timings["total_ms"] = _ms(started)
    return {
        "results": rows[:top_k],
        "retrieval": {
            "status": "degraded" if dense_error else "ok",
            "dense_candidates": len(dense_ids),
            "lexical_candidates": len(lexical_ids),
            "fused_candidates": len(fused),
            "reranker_used": reranker_used,
            "dense_error": dense_error,
            "mode": {
                "dense": use_dense,
                "lexical": use_lexical,
                "reranker": use_reranker,
                "reranker_weight": reranker_weight,
            },
            "timings": timings,
        },
    }


def _get_lexical_index() -> tuple[BM25Index, dict[str, LexicalDocument]]:
    global _index, _documents, _index_collection_name
    collection_name = current_games_collection_name()
    signature = _cache_signature()
    cache_key = f"{collection_name}:{signature[0]}:{signature[1]}"
    if _index is None or _index_collection_name != cache_key:
        with _lock:
            if _index is None or _index_collection_name != cache_key:
                cache_path = CACHE_PATH
                pointer = Path(CHROMA_PERSIST_DIR) / "current_index.json"
                if pointer.exists():
                    value = json.loads(pointer.read_text(encoding="utf-8"))
                    cache_path = Path(CHROMA_PERSIST_DIR) / str(value.get("cache", cache_path.name))
                records = (
                    json.loads(cache_path.read_text(encoding="utf-8"))
                    if cache_path.exists()
                    else []
                )
                documents = []
                for record in records:
                    doc_id, metadata, text = build_chunk(
                        record["appid"], record["detail"], record.get("user_tags")
                    )
                    document = LexicalDocument(doc_id=doc_id, text=text, metadata=metadata)
                    documents.append(document)
                _documents = {document.doc_id: document for document in documents}
                _index = BM25Index(documents)
                _index_collection_name = cache_key
    return _index, _documents


def _metadata_predicate(where: dict | None, *, required_genre: str | None = None):
    if not where:
        return None

    def predicate(metadata: dict) -> bool:
        return _matches(metadata, where) and _genre_matches(metadata, required_genre)

    return predicate


_CHROMA_FILTER_FIELDS = {"is_free", "release_year", "has_multiplayer", "metacritic"}


def _split_where(where: dict | None) -> tuple[dict | None, dict | None]:
    """Split filters supported by the frozen Chroma schema from cache filters."""
    if not where:
        return None, None
    if "$and" in where:
        chroma, post = [], []
        for child in where["$and"]:
            c, p = _split_where(child)
            if c:
                chroma.append(c)
            if p:
                post.append(p)
        return ({"$and": chroma} if len(chroma) > 1 else (chroma[0] if chroma else None)), ({"$and": post} if len(post) > 1 else (post[0] if post else None))
    chroma, post = {}, {}
    for key, value in where.items():
        (chroma if key in _CHROMA_FILTER_FIELDS else post)[key] = value
    return chroma or None, post or None


def _genre_matches(metadata: dict, required_genre: str | None) -> bool:
    if not required_genre:
        return True
    genres = ", ".join(
        str(metadata.get(key, ""))
        for key in ("genres", "categories", "gameplay_modes")
    ).casefold()
    return required_genre.casefold() in genres


def _matches(metadata: dict, expression: dict) -> bool:
    if "$and" in expression:
        return all(_matches(metadata, child) for child in expression["$and"])
    for key, expected in expression.items():
        actual = metadata.get(key)
        if isinstance(expected, dict):
            if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                return False
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if high == low:
        return [1.0 for _ in values]
    return [(float(value) - low) / (high - low) for value in values]
