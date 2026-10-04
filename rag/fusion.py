from __future__ import annotations


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    *,
    rank_constant: int = 60,
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rank_constant + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
