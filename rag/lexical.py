from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass


TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.lower())


@dataclass(frozen=True)
class LexicalDocument:
    doc_id: str
    text: str
    metadata: dict


class BM25Index:
    def __init__(self, documents: list[LexicalDocument], k1: float = 1.5, b: float = 0.75):
        self.documents = documents
        self.k1 = k1
        self.b = b
        self.tokens = [tokenize(document.text) for document in documents]
        self.lengths = [len(tokens) for tokens in self.tokens]
        self.avg_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        document_frequency: Counter[str] = Counter()
        for tokens in self.tokens:
            document_frequency.update(set(tokens))
        count = len(documents)
        self.idf = {
            token: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
            for token, frequency in document_frequency.items()
        }

    def search(self, query: str, top_k: int, predicate=None) -> list[tuple[LexicalDocument, float]]:
        query_tokens = tokenize(query)
        scored = []
        for document, tokens, length in zip(self.documents, self.tokens, self.lengths):
            if predicate and not predicate(document.metadata):
                continue
            frequencies = Counter(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies.get(token, 0)
                if not frequency:
                    continue
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / (self.avg_length or 1)
                )
                score += self.idf.get(token, 0.0) * frequency * (self.k1 + 1) / denominator
            if score > 0:
                scored.append((document, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]
