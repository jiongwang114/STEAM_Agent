from ..config import STEAM_STORE_URL

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def search_steam_store(query: str, max_results: int = 10) -> dict:
    """Look up current Steam store facts using a short title or category query."""
    query = str(query or "").strip()[:120]
    max_results = max(1, min(int(max_results), 20))
    if not query:
        return {"error": "query_required"}
    search_data = _search_store(query)

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
        appid = item.get("id", detail.get("steam_appid"))
        results.append({
            "appid": appid,
            "name": detail.get("name", item.get("name", "Unknown")),
            "price": _extract_price(detail),
            "metacritic": detail.get("metacritic", {}).get("score"),
            "tags": [g["description"] for g in detail.get("genres", [])],
            "header_image": detail.get("header_image", ""),
            "short_description": detail.get("short_description", ""),
            "store_url": f"https://store.steampowered.com/app/{appid}/" if appid else "",
        })

    return {"results": results}


def _search_store(query: str) -> dict:
    import urllib.request
    import json

    from urllib.parse import urlencode
    url = f"{STEAM_STORE_URL}/storesearch/?{urlencode({'term': query, 'cc': 'cn', 'l': 'schinese'})}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        return json.loads(resp.read())


def _fetch_app_detail_sync(appid: int) -> dict | None:
    import json
    import urllib.request

    url = f"{STEAM_STORE_URL}/appdetails?appids={appid}&cc=cn"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = json.loads(resp.read())
            app_data = data.get(str(appid), {})
            return app_data.get("data") if app_data.get("success") else None
    except Exception:
        return None


def _fetch_app_details_sync(appids: list[int]) -> list[dict | None]:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(5, len(appids) or 1)) as pool:
        return list(pool.map(_fetch_app_detail_sync, appids))


def _extract_price(detail: dict) -> dict:
    price_overview = detail.get("price_overview")
    if not price_overview:
        return {"currency": "N/A", "final": 0, "initial": 0, "discount_percent": 0}
    return {
        "currency": price_overview.get("currency", "N/A"),
        "final": price_overview.get("final", 0),
        "initial": price_overview.get("initial", price_overview.get("final", 0)),
        "discount_percent": price_overview.get("discount_percent", 0),
    }
