from ..config import STEAM_STORE_URL

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def search_steam_store(query: str, max_results: int = 10) -> dict:
    """
    Search the official Steam store by game name or simple keywords.
    Uses Steam's /storesearch endpoint which does TEXT-BASED name matching only —
    NOT semantic/embedding search.

    IMPORTANT query rules:
    - Use short, specific terms: a game title ("Elden Ring"), a genre tag ("roguelike"),
      or a simple keyword combo ("open world survival").
    - Do NOT pass long natural-language descriptions like "games similar to Dark Souls
      with crafting mechanics" — Steam's text search cannot understand these.
    - If a query returns no results, try a single broader keyword instead of a sentence.
    - For discovering games by vibe/theme/feel, use rag_search_similar_games instead.
    """
    try:
        search_data = _search_store(query)
    except Exception as exc:
        return {"error": str(exc)}

    items = search_data.get("items", [])
    if not items:
        return {"results": []}

    appids = [item["id"] for item in items[:max_results] if "id" in item]
    if not appids:
        return {"results": []}

    details_list = _fetch_app_details_sync(appids)

    results = []
    for i, detail in enumerate(details_list):
        if detail is None:
            continue
        item = items[i] if i < len(items) else {}
        results.append({
            "appid": item.get("id", detail.get("steam_appid")),
            "name": detail.get("name", item.get("name", "Unknown")),
            "price": _extract_price(detail),
            "metacritic": detail.get("metacritic", {}).get("score"),
            "tags": [g["description"] for g in detail.get("genres", [])],
            "header_image": detail.get("header_image", ""),
            "short_description": detail.get("short_description", ""),
        })

    return {"results": results}


def _search_store(query: str) -> dict:
    import urllib.request
    import json

    url = f"{STEAM_STORE_URL}/storesearch/?term={urllib.request.quote(query)}&cc=us&l=english"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        return json.loads(resp.read())


def _fetch_app_detail_sync(appid: int) -> dict | None:
    import json
    import urllib.request

    url = f"{STEAM_STORE_URL}/appdetails?appids={appid}&cc=us"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = json.loads(resp.read())
            app_data = data.get(str(appid), {})
            return app_data.get("data") if app_data.get("success") else None
    except Exception:
        return None


def _fetch_app_details_sync(appids: list[int]) -> list[dict | None]:
    results = []
    for appid in appids:
        results.append(_fetch_app_detail_sync(appid))
    return results


def _extract_price(detail: dict) -> dict:
    price_overview = detail.get("price_overview")
    if not price_overview:
        return {"currency": "N/A", "final": 0}
    return {
        "currency": price_overview.get("currency", "N/A"),
        "final": price_overview.get("final", 0),
    }
