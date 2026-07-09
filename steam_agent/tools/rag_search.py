from pathlib import Path
from ..rag.translate import translate_to_english
from ..rag.vector_store import get_games_collection
from ..rag.embedder import embed_query

CACHE_PATH = Path(__file__).resolve().parent.parent / "rag" / "chroma_data" / "game_cache.json"
_cache_images: dict[str, str] | None = None


def _load_cache_images() -> dict[str, str]:
    """Load header_image map from game_cache.json, lazy."""
    global _cache_images
    if _cache_images is None:
        import json
        _cache_images = {}
        if CACHE_PATH.exists():
            with open(CACHE_PATH, encoding="utf-8") as f:
                games = json.load(f)
                for g in games:
                    appid = str(g.get("appid", ""))
                    img = g.get("detail", {}).get("header_image", "")
                    if appid and img:
                        _cache_images[appid] = img
    return _cache_images


def rag_search_similar_games(
    query: str,
    top_k: int = 10,
    free_only: bool = False,
    min_year: int | None = None,
    has_multiplayer: bool | None = None,
    min_metacritic: int | None = None,
    min_similarity: float = 0.3,
) -> dict:
    """
    Semantic search over the game knowledge base.
    Translates Chinese queries to English, embeds, and queries Chroma.

    Hard filters (objective, 100% accurate metadata constraints):
    - free_only: if True, only return free-to-play games
    - min_year: only return games released in or after this year
    - has_multiplayer: if True, only return multiplayer/co-op games;
      if False, only single-player. If None, no filter.
    - min_metacritic: only return games with metacritic >= this score

    Quality control:
    - min_similarity: drop results below this cosine similarity score.
      Default 0.3. Set to 0 to disable.

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

    # Hard constraints only — no genres or user_tags in metadata.
    conditions = []
    if free_only:
        conditions.append({"is_free": True})
    if min_year is not None:
        conditions.append({"release_year": {"$gte": min_year}})
    if has_multiplayer is not None:
        conditions.append({"has_multiplayer": has_multiplayer})
    if min_metacritic is not None:
        conditions.append({"metacritic": {"$gte": min_metacritic}})

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
            sim = round(1 - raw["distances"][0][i], 4) if raw.get("distances") else 0.0
            if min_similarity > 0 and sim < min_similarity:
                continue
            desc = raw["documents"][0][i].strip() if raw.get("documents") else ""
            results.append({
                "appid": raw["ids"][0][i],
                "name": meta.get("name", "Unknown"),
                "similarity_score": sim,
                "description": desc,
                "is_free": meta.get("is_free", False),
                "release_year": meta.get("release_year", 0),
                "has_multiplayer": meta.get("has_multiplayer", False),
                "metacritic": meta.get("metacritic", 0),
                "header_image": _load_cache_images().get(raw["ids"][0][i], ""),
                "store_url": f"https://store.steampowered.com/app/{raw['ids'][0][i]}/",
            })

    return {"results": results}


def _contains_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)
