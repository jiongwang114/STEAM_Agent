from ..rag.translate import translate_to_english
from ..rag.vector_store import get_games_collection


def rag_search_similar_games(
    query: str,
    top_k: int = 5,
    filter_tags: list[str] | None = None,
) -> dict:
    """
    Semantic search over the game knowledge base.
    Translates Chinese queries to English, embeds, and queries Chroma.
    Supports optional tag-based filtering (hybrid retrieval).

    IMPORTANT: Call this tool at most once per user turn. If results are poor
    (similarity_score < 0.4), do NOT retry with different keywords — use
    search_steam_store instead or ask the user for more details.
    """
    if _contains_chinese(query):
        search_query = translate_to_english(query)
    else:
        search_query = query

    collection = get_games_collection()

    kwargs = {
        "query_texts": [search_query],
        "n_results": top_k,
    }
    if filter_tags:
        kwargs["where"] = {"tags": {"$in": filter_tags}}

    raw = collection.query(**kwargs)

    results = []
    if raw["ids"] and raw["ids"][0]:
        for i in range(len(raw["ids"][0])):
            meta = raw["metadatas"][0][i] if raw["metadatas"] else {}
            results.append({
                "appid": raw["ids"][0][i],
                "name": meta.get("name", "Unknown"),
                "similarity_score": round(1 - raw["distances"][0][i], 4) if raw.get("distances") else 0.0,
                "description": meta.get("description", ""),
                "tags": meta.get("tags", []),
                "review_summary": meta.get("review_summary", ""),
            })

    return {"results": results}


def _contains_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)
