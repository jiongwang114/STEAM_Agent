"""
Scrape Steam user tags from store pages and merge into game cache.
Tags are extracted from the InitAppTagModal JS blob on each store page.

Usage:
    python -m steam_agent.tools.add_user_tags           # scrape and save
    python -m steam_agent.tools.add_user_tags --dry-run  # count only
"""

import argparse
import json
import re
import time
import urllib.request as ur
from pathlib import Path
from collections import Counter

CACHE_PATH = Path(__file__).resolve().parent.parent / "rag" / "chroma_data" / "game_cache.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def fetch_user_tags(appid: int) -> list[str]:
    """Extract user-defined tags from a Steam store page."""
    url = f"https://store.steampowered.com/app/{appid}/"
    try:
        req = ur.Request(url, headers=HEADERS)
        with ur.urlopen(req, timeout=30.0) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    # Steam: InitAppTagModal(730, [{...}, ...], [], "str", "str", null, false);
    match = re.search(r"InitAppTagModal\(\s*\d+\s*,\s*(\[\{.*?\}\])", html)
    if not match:
        return []

    tag_block = match.group(1)
    names = re.findall(r'"name":"([^"]+)"', tag_block)
    return names


def main():
    parser = argparse.ArgumentParser(description="Scrape Steam user tags")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max games to scrape (0=all)")
    parser.add_argument("--force", action="store_true", help="Re-scrape already tagged games")
    args = parser.parse_args()

    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)

    already = sum(1 for r in records if r.get("user_tags"))
    print(f"Total: {len(records)} games | Already tagged: {already} | Need: {len(records) - already}")

    if args.dry_run:
        return

    all_tags: Counter = Counter()
    done = 0
    limit = args.limit or len(records)

    for i, rec in enumerate(records):
        if done >= limit:
            break

        if not args.force and rec.get("user_tags"):
            for t in rec["user_tags"]:
                all_tags[t] += 1
            continue

        appid = rec["appid"]
        name = rec["detail"].get("name", str(appid))
        tags = fetch_user_tags(appid)
        rec["user_tags"] = tags

        for t in tags:
            all_tags[t] += 1
        done += 1

        if done % 20 == 0:
            print(f"  {done} done | last: {name[:30]} -> {len(tags)} tags")
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)

        time.sleep(1.2)

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"\nTagged {done} new games.")
    print(f"Tag distribution (top 40):")
    for tag, count in all_tags.most_common(40):
        pct = count / len([r for r in records if r.get("user_tags")]) * 100
        print(f"  {tag}: {count} ({pct:.0f}%)")

    if done > 0:
        print(f"\nNext: python -m steam_agent.rag.ingest --from-cache")


if __name__ == "__main__":
    main()
