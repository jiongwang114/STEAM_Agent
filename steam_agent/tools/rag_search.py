from ..rag.translate import translate_to_english
from ..rag.vector_store import get_games_collection
from ..rag.embedder import embed_query


def rag_search_similar_games(
    query: str,
    top_k: int = 5,
    filter_tags: list[str] | None = None,
    free_only: bool = False,
    min_metacritic: int | None = None,
    min_year: int | None = None,
) -> dict:
    """
    Semantic search over the game knowledge base.
    Translates Chinese queries to English, embeds, and queries Chroma.
    Supports optional filtering.

    Filters (applied as exact metadata constraints, not semantic):
    - filter_tags: only return games matching these genre tags
    - free_only: if True, only return free-to-play games
    - min_metacritic: only return games with metacritic >= this score
    - min_year: only return games released in or after this year

    IMPORTANT: Call this tool at most once per user turn. If results are poor
    (similarity_score < 0.4), do NOT retry with different keywords — use
    search_steam_store instead or ask the user for more details.
    """
    if _contains_chinese(query):
        search_query = translate_to_english(query)
    else:
        search_query = query

    collection = get_games_collection()
    query_embedding = embed_query([search_query])

    # Build ChromaDB where clause from metadata filters.
    conditions = []
    if filter_tags:
        conditions.append({"tags": {"$in": filter_tags}})
    if free_only:
        conditions.append({"is_free": True})
    if min_metacritic is not None:
        conditions.append({"metacritic": {"$gte": min_metacritic}})
    if min_year is not None:
        conditions.append({"release_year": {"$gte": min_year}})

    where = None
    if len(conditions) == 1:
        where = conditions[0]
    elif len(conditions) > 1:
        where = {"$and": conditions}

    kwargs: dict = {
        "query_embeddings": query_embedding,
        "n_results": top_k,
    }
    if where:
        kwargs["where"] = where

    raw = collection.query(**kwargs)

    results = []
    if raw["ids"] and raw["ids"][0]:
        for i in range(len(raw["ids"][0])):
            meta = raw["metadatas"][0][i] if raw["metadatas"] else {}
            results.append({
                "appid": raw["ids"][0][i],
                "name": meta.get("name", "Unknown"),
                "similarity_score": round(1 - raw["distances"][0][i], 4) if raw.get("distances") else 0.0,
                "tags": meta.get("tags", []),
                "is_free": meta.get("is_free", False),
                "metacritic": meta.get("metacritic", 0),
                "release_year": meta.get("release_year", 0),
            })

    return {"results": results}


def _contains_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)
