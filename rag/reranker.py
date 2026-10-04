from __future__ import annotations

import math
import threading

from config import RERANKER_ENABLED, RERANKER_MODEL, RERANKER_REVISION


_model = None
_lock = threading.Lock()


def rerank(query: str, documents: list[str]) -> tuple[list[float], bool]:
    if not RERANKER_ENABLED or not documents:
        return [0.0] * len(documents), False
    try:
        model = _get_model()
        tokenizer = model.tokenizer
        query_tokens = tokenizer.encode(query, add_special_tokens=False)
        maximum = model.max_length or min(tokenizer.model_max_length, 512)
        size = max(32, maximum - min(len(query_tokens), maximum // 2) - 8)
        pairs, owners = [], []
        for owner, document in enumerate(documents):
            tokens = tokenizer.encode(document, add_special_tokens=False)
            for start in range(0, max(1, len(tokens)), max(1, size - 32)):
                pairs.append([query, tokenizer.decode(tokens[start:start+size], skip_special_tokens=True)])
                owners.append(owner)
        raw_scores = model.predict(pairs, batch_size=16)
        scores = [0.0] * len(documents)
        for owner, score in zip(owners, raw_scores):
            scores[owner] = max(scores[owner], _sigmoid(float(score)))
        return scores, True
    except Exception:
        # Retrieval remains available if the optional local reranker cannot load.
        return [0.0] * len(documents), False


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import CrossEncoder

                _model = CrossEncoder(RERANKER_MODEL, revision=RERANKER_REVISION or None)
    return _model


def _sigmoid(value: float) -> float:
    if value >= 0:
        factor = math.exp(-value)
        return 1 / (1 + factor)
    factor = math.exp(value)
    return factor / (1 + factor)
