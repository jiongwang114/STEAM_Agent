from tools.rag_search import rag_search_similar_games
from tools.search_plan import SearchPlan


def test_search_plan_normalizes_whitespace():
    plan = SearchPlan(query="  cooperative survival  ", genre="  Survival  ")
    assert plan.query == "cooperative survival"
    assert plan.genre == "Survival"


def test_rag_rejects_invalid_structured_plan():
    result = rag_search_similar_games(
        plan={"query": "games", "min_year": 1800},
    )
    assert result["results"] == []
    assert result["retrieval"]["status"] == "invalid_input"


def test_rag_accepts_structured_plan(monkeypatch):
    captured = {}

    def fake_search(query, **kwargs):
        captured.update(query=query, **kwargs)
        return {"results": [], "retrieval": {}}

    monkeypatch.setattr("tools.rag_search.hybrid_search", fake_search)
    result = rag_search_similar_games(
        plan={"query": "co-op games", "free_only": True, "top_k": 3},
    )
    assert result["results"] == []
    assert captured["query"] == "co-op games"
    assert captured["top_k"] == 3
    assert captured["where"] == {"is_free": True}
