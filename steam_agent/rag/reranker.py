from __future__ import annotations

import math
import threading

from ..config import RERANKER_ENABLED, RERANKER_MODEL


_model = None
_lock = threading.Lock()


def rerank(query: str, documents: list[str]) -> tuple[list[float], bool]:
    if not RERANKER_ENABLED or not documents:
        return [0.0] * len(documents), False
    try:
        model = _get_model()
        raw_scores = model.predict([[query, document] for document in documents])
        return [_sigmoid(float(score)) for score in raw_scores], True
    except Exception:
        # Retrieval remains available if the optional local reranker cannot load.
        return [0.0] * len(documents), False


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import CrossEncoder

                _model = CrossEncoder(RERANKER_MODEL)
    return _model


def _sigmoid(value: float) -> float:
    if value >= 0:
        factor = math.exp(-value)
        return 1 / (1 + factor)
    factor = math.exp(value)
    return factor / (1 + factor)
