from rag import vector_store


def test_index_compatibility_requires_embedding_dimension(monkeypatch):
    monkeypatch.setattr(vector_store, "index_manifest", lambda: {
        "embedding_model": vector_store.EMBEDDING_MODEL,
        "game_count": 1,
    })
    monkeypatch.setattr(vector_store, "_embedding_dimension", lambda: 768)
    result = vector_store.index_compatibility()
    assert result["compatible"] is False
    assert result["mismatches"]["embedding_dimension"]["actual"] is None


def test_index_compatibility_accepts_matching_embedding_dimension(monkeypatch):
    monkeypatch.setattr(vector_store, "index_manifest", lambda: {
        "embedding_model": vector_store.EMBEDDING_MODEL,
        "embedding_dimension": 768,
        "game_count": 1,
    })
    monkeypatch.setattr(vector_store, "_embedding_dimension", lambda: 768)
    result = vector_store.index_compatibility()
    assert "embedding_dimension" not in result["mismatches"]
