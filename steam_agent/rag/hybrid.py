from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ..config import (
    CHROMA_PERSIST_DIR,
    RAG_DENSE_CANDIDATES,
    RAG_LEXICAL_CANDIDATES,
    RAG_RERANK_CANDIDATES,
    RAG_RERANK_WEIGHT,
)
from .embedder import embed_query
from .fusion import reciprocal_rank_fusion
from .ingest import build_chunk
from .lexical import BM25Index, LexicalDocument
from .reranker import rerank
from .vector_store import current_games_collection_name, get_games_collection


CACHE_PATH = Path(CHROMA_PERSIST_DIR) / "game_cache.json"
_index: BM25Index | None = None
_documents: dict[str, LexicalDocument] = {}
_index_collection_name = ""
_lock = threading.Lock()


def hybrid_search(
    query: str,
    *,
    top_k: int,
    where: dict | None = None,
    use_dense: bool = True,
    use_lexical: bool = True,
    use_reranker: bool = True,
    reranker_weight: float = RAG_RERANK_WEIGHT,
) -> dict:
    timings = {}
    started = time.perf_counter()
    index, documents = _get_lexical_index()
    timings["index_load_ms"] = _ms(started)

    dense_started = time.perf_counter()
    dense_error = ""
    if use_dense:
        try:
            dense_raw = get_games_collection().query(
                query_embeddings=embed_query([query]),
                n_results=RAG_DENSE_CANDIDATES,
                **({"where": where} if where else {}),
            )
        except Exception as exc:
            dense_raw = {"ids": [[]], "distances": [[]]}
            dense_error = type(exc).__name__
    else:
        dense_raw = {"ids": [[]], "distances": [[]]}
    dense_ids = list(dense_raw.get("ids", [[]])[0])
    dense_distances = list(dense_raw.get("distances", [[]])[0])
    dense_scores = {
        doc_id: 1 - float(dense_distances[index])
        for index, doc_id in enumerate(dense_ids)
        if index < len(dense_distances)
    }
    timings["dense_ms"] = _ms(dense_started)

    lexical_started = time.perf_counter()
    predicate = _metadata_predicate(where)
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
    candidates = [doc_id for doc_id, _ in fused[:RAG_RERANK_CANDIDATES]]
    fusion_scores = dict(fused)
    timings["fusion_ms"] = _ms(fusion_started)

    rerank_started = time.perf_counter()
    candidate_docs = [documents[doc_id] for doc_id in candidates if doc_id in documents]
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
    if _index is None or _index_collection_name != collection_name:
        with _lock:
            if _index is None or _index_collection_name != collection_name:
                records = (
                    json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                    if CACHE_PATH.exists()
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
                _index_collection_name = collection_name
    return _index, _documents


def _metadata_predicate(where: dict | None):
    if not where:
        return None

    def predicate(metadata: dict) -> bool:
        return _matches(metadata, where)

    return predicate


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
