from pathlib import Path
import time
from ..rag.translate import translate_to_english
from ..rag.hybrid import hybrid_search

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
    """Find games by semantic similarity with optional objective metadata filters."""
    top_k = max(1, min(int(top_k), 20))
    min_similarity = max(0.0, min(float(min_similarity), 1.0))
    query = str(query or "").strip()[:1000]
    if not query:
        return {"results": [], "retrieval": {"status": "invalid_input"}}
    started = time.perf_counter()
    translation_started = time.perf_counter()
    translation_status = "not_needed"
    if _contains_chinese(query):
        try:
            search_query = translate_to_english(query) or query
            translation_status = "translated" if search_query != query else "fallback_original"
        except Exception:
            search_query = query
            translation_status = "fallback_original"
    else:
        search_query = query
    translation_ms = round((time.perf_counter() - translation_started) * 1000, 3)

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

    raw = hybrid_search(search_query, top_k=top_k, where=where)

    results = []
    for row in raw.get("results", []):
        meta = row["metadata"]
        if row.get("rerank_score") is not None:
            sim = float(row.get("final_score", row["rerank_score"]))
        elif row.get("dense_similarity") is not None:
            sim = float(row["dense_similarity"])
        else:
            sim = min(1.0, float(row.get("fusion_score", 0)) * 30)
        sim = round(sim, 4)
        if min_similarity > 0 and sim < min_similarity:
            continue
        appid = row["appid"]
        desc = row["document"].strip()
        results.append({
            "appid": appid,
            "name": meta.get("name", "Unknown"),
            "similarity_score": sim,
            "dense_similarity": row.get("dense_similarity"),
            "lexical_score": round(float(row.get("lexical_score", 0)), 4),
            "fusion_score": round(float(row.get("fusion_score", 0)), 6),
            "rerank_score": row.get("rerank_score"),
            "final_score": row.get("final_score"),
            "description": desc,
            "is_free": meta.get("is_free", False),
            "release_year": meta.get("release_year", 0),
            "has_multiplayer": meta.get("has_multiplayer", False),
            "metacritic": meta.get("metacritic", 0),
            "header_image": _load_cache_images().get(appid, ""),
            "store_url": f"https://store.steampowered.com/app/{appid}/",
        })

    retrieval = dict(raw.get("retrieval", {}))
    retrieval.setdefault("timings", {})["translation_ms"] = translation_ms
    retrieval["translation_status"] = translation_status
    retrieval["timings"]["end_to_end_ms"] = round(
        (time.perf_counter() - started) * 1000, 3
    )
    return {"results": results, "retrieval": retrieval}


def _contains_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)
