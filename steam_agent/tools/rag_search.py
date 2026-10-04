from pathlib import Path
import time
from ..rag.translate import translate_to_english
from ..rag.hybrid import hybrid_search
from .search_plan import SearchPlan, ValidationError, validate_search_plan

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
    plan: SearchPlan | dict,
) -> dict:
    """
    从本地 Steam 游戏索引中检索与用户需求相似的游戏候选。

    适用场景：
    - 用户要求推荐、寻找、挑选或比较 Steam 游戏；
    - 用户按玩法、类型、氛围、主题或相似游戏进行发现；
    - 用户要求根据 Steam 游戏库或游玩记录寻找相似游戏。

    不适用场景：
    - 查询当前价格、折扣、商店评分或商店页面详情；
    - 查询用户的 Steam 游戏库或游玩时长；
    - 查询历史对话原文；
    - 用户只是在询问游戏概念、玩法机制或一般知识。

    参数说明：
    - plan：符合 SearchPlan 结构的检索计划。query 用简洁自然语言描述用户想寻找的游戏体验；
      应包含核心玩法、类型、氛围、主题和明确的排斥条件。
      不要加入价格、折扣或未经用户提出的偏好。
      计划中的结构化字段只用于明确、可验证的硬约束。

    调用要求：
    - 未明确提出的结构化筛选条件不要自行猜测。
    - 将用户的正向偏好和明确排斥条件写入 query。
    - 不要把多个无关请求拼成一个模糊查询。
    - 查询结果为空时，可以根据原请求改写 query 后再次检索。
    - 不要因为结果中包含商店链接或评分字段，就把本工具当作实时商店查询工具。

    返回内容：
    - results：候选游戏列表；
    - 每个候选通常包含 appid、name、description、相似度、
      发行年份、多人属性、Metacritic 评分、header_image 和 store_url；
    - retrieval：本次检索的状态和检索元数据。

    重要限制：
    - 结果是推荐候选和索引证据，不代表当前价格、折扣或实时商店状态。
    - 只能使用返回结果中实际出现的游戏和字段组织后续回答。
    """
    try:
        parsed = validate_search_plan(plan)
    except (ValidationError, TypeError, ValueError) as exc:
        return {
            "results": [],
            "retrieval": {
                "status": "invalid_input",
                "validation_error": str(exc),
            },
        }
    query = parsed.query
    top_k = parsed.top_k
    free_only = parsed.free_only
    min_year = parsed.min_year
    has_multiplayer = parsed.has_multiplayer
    genre = parsed.genre
    min_metacritic = parsed.min_metacritic
    min_similarity = parsed.min_similarity
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

    raw = hybrid_search(search_query, top_k=top_k, where=where, required_genre=genre)

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
