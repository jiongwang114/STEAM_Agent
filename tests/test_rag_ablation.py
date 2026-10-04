from tests import rag_ablation


def test_ablation_modes_disable_reranker(monkeypatch):
    captured = {}

    def fake_search(query, **kwargs):
        captured.update(kwargs)
        return {"results": [], "retrieval": {"reranker_used": False}}

    monkeypatch.setattr(rag_ablation, "hybrid_search", fake_search)
    result = rag_ablation.run_case(
        {"id": "1", "query": "survival", "relevant_appids": "1;2"},
        "rrf",
        10,
    )
    assert captured["use_dense"] is True
    assert captured["use_lexical"] is True
    assert captured["use_reranker"] is False
    assert result["reranker_used"] is False
