"""
Batch expand game cache by searching Steam store for niche genre keywords.
No new APIs needed — uses storesearch (existing) + appdetails (existing).

Usage:
    python -m steam_agent.tools.expand_cache              # search + fetch + save
    python -m steam_agent.tools.expand_cache --dry-run    # search only, show counts
"""

import argparse
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parent.parent / "rag" / "chroma_data" / "game_cache.json"
STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

NICHE_KEYWORDS = [
    "metroidvania", "city builder", "visual novel", "4X strategy",
    "bullet hell", "walking simulator", "dating sim", "automation game",
    "colony sim", "deck builder", "farming sim", "grand strategy",
    "immersive sim", "rhythm game", "souls like", "space sim",
    "tactical rpg", "tower defense", "puzzle platformer", "fishing game",
    "stealth action", "zombie survival", "post apocalyptic",
    "roguelite action", "isometric rpg", "horror survival",
    "hand drawn platformer", "turn based strategy", "real time strategy",
    "cozy game", "adventure point and click", "roguelike dungeon crawler",
    "management tycoon", "open world survival craft", "story rich choices matter",
    "hack and slash loot", "retro pixel platformer",
]


def store_search(term: str, max_results: int = 30) -> list[dict]:
    """Search Steam store by term, return list of {id, name}."""
    url = f"{STORE_SEARCH_URL}?term={urllib.request.quote(term)}&cc=us&l=english"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            data = json.loads(resp.read())
            items = data.get("items", [])
            return [{"id": item["id"], "name": item.get("name", "")} for item in items[:max_results]]
    except Exception as e:
        print(f"  [WARN] '{term}' search failed: {e}")
        return []


def fetch_app_detail(appid: int) -> dict | None:
    """Fetch game detail from Steam appdetails. Returns raw detail dict or None."""
    url = f"{APP_DETAILS_URL}?appids={appid}&cc=us&l=en"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            data = json.loads(resp.read())
            app_data = data.get(str(appid), {})
            return app_data.get("data") if app_data.get("success") else None
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Expand game cache via Steam store search")
    parser.add_argument("--dry-run", action="store_true", help="Search only, show counts, don't fetch details")
    args = parser.parse_args()

    # Load existing cache
    existing_ids = set()
    cache_records = []
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache_records = json.load(f)
        existing_ids = {rec["appid"] for rec in cache_records}
        print(f"现有缓存: {len(cache_records)} 款游戏\n")

    # Step 1: Search each keyword and collect unique appids
    all_found: dict[int, str] = {}  # appid -> name
    for kw in NICHE_KEYWORDS:
        items = store_search(kw)
        for item in items:
            if item["id"] not in existing_ids and item["id"] not in all_found:
                all_found[item["id"]] = item["name"]
        print(f"  '{kw}': {len(items)} results, {len(all_found)} unique new total")
        time.sleep(0.3)

    new_appids = list(all_found.keys())
    print(f"\n新发现的独有 appid: {len(new_appids)} 个")
    print(f"预计库大小: {len(cache_records) + len(new_appids)} 款")

    if args.dry_run:
        print("\n[Dry run] 不拉取详情。用以下命令实际执行:")
        print("  python -m steam_agent.tools.expand_cache")
        return

    if not new_appids:
        print("没有新游戏，无需执行。")
        return

    # Step 2: Fetch details for each new appid
    print(f"\n开始拉取 {len(new_appids)} 款游戏详情...")
    added = 0
    for i, appid in enumerate(new_appids):
        detail = fetch_app_detail(appid)
        if detail is None or detail.get("type") != "game":
            if (i + 1) % 20 == 0:
                print(f"  已处理 {i+1}/{len(new_appids)}... (新增 {added})")
            continue

        cache_records.append({"appid": appid, "detail": detail})
        added += 1

        if (i + 1) % 20 == 0:
            print(f"  已处理 {i+1}/{len(new_appids)}... (新增 {added})")
            time.sleep(0.5)

    # Step 3: Save expanded cache
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache_records, f, ensure_ascii=False, indent=2)
    print(f"\n完成！缓存: {len(cache_records)} 款游戏 (+{added} new)")
    print(f"下一步: python -m steam_agent.rag.ingest --from-cache")


if __name__ == "__main__":
    main()
