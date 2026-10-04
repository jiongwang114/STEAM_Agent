import pytest

from rag import ingest, hybrid


def test_empty_build_does_not_open_database(monkeypatch):
    monkeypatch.setattr(ingest, '_get_client', lambda: pytest.fail('Must not touch database'))
    with pytest.raises(ValueError, match='empty index'):
        ingest._build_collection([], mode='cache')


def test_description_and_modes_are_preserved_without_html():
    _, metadata, text = ingest.build_chunk(42, {
        'name': 'Ocean Explorer', 'short_description': '<p>Explore &amp; build.</p>',
        'about_the_game': '<h2>Survival</h2><p>Collect resources and build underwater bases.</p>',
        'categories': [{'description': 'Single-player'}, {'description': 'Online Co-op'}],
        'genres': [{'description': 'Adventure'}], 'supported_languages': 'English<strong>*</strong>',
        'release_date': {'date': '20 Jan, 2012'},
    })
    assert 'Collect resources and build underwater bases.' in text
    assert 'Explore & build.' in text
    assert '<' not in text
    assert metadata['has_coop'] and metadata['has_singleplayer']
    assert metadata['release_year'] == 2012


def test_dense_chunks_are_merged_into_unique_game_candidates(monkeypatch):
    from rag.lexical import LexicalDocument
    documents = {str(i): LexicalDocument(str(i), f'Game {i}', {}) for i in (1, 2)}
    monkeypatch.setattr(hybrid, '_get_lexical_index', lambda: (None, documents))
    monkeypatch.setattr(hybrid, 'embed_query', lambda _: [[0.0]])
    class Collection:
        def query(self, **kwargs):
            return {'ids': [['1:0', '1:1', '2:0']], 'distances': [[0.1, 0.2, 0.3]]}
    monkeypatch.setattr(hybrid, 'get_games_collection', lambda: Collection())
    result = hybrid.hybrid_search('exploration', top_k=3, use_lexical=False, use_reranker=False)
    assert [row['appid'] for row in result['results']] == ['1', '2']
    assert result['results'][0]['dense_similarity'] == pytest.approx(0.9)


def test_persistent_index_opens_with_explicit_query_vectors(tmp_path, monkeypatch):
    import chromadb
    from rag import vector_store
    client = chromadb.PersistentClient(path=str(tmp_path))
    collection = client.create_collection('games')
    collection.add(ids=['42:0'], embeddings=[[1.0, 0.0]], documents=['Ocean survival'])
    monkeypatch.setattr(vector_store, '_get_client', lambda: client)
    monkeypatch.setattr(vector_store, 'current_games_collection_name', lambda: 'games')
    result = vector_store.get_games_collection().query(query_embeddings=[[1.0, 0.0]], n_results=1)
    assert result['ids'] == [['42:0']]
