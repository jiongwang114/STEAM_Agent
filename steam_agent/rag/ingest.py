"""
Offline data ingestion script for the Steam game knowledge base.

Usage:
    python -m steam_agent.rag.ingest                     # fetch from API + rebuild (saves cache)
    python -m steam_agent.rag.ingest --from-cache          # rebuild from local cache (no API calls)
    python -m steam_agent.rag.ingest --mode append         # fetch new games only
"""

import argparse
import json
import time
from pathlib import Path

import httpx

from ..config import STEAM_STORE_URL
from .embedder import embed
from .vector_store import _get_client, get_games_collection

STEAM_TOP_GAMES_URL = "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/"
STEAM_APP_LIST_URL = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"

DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_PATH = DATA_DIR / "game_cache.json"


def fetch_top_appids(count: int = 250) -> list[int]:
    appids = []
    try:
        response = httpx.get(STEAM_TOP_GAMES_URL, params={"key": "PLACEHOLDER"}, timeout=15.0)
        data = response.json()
        ranks = data.get("response", {}).get("ranks", [])
        appids = [r["appid"] for r in ranks[:count]]
    except httpx.HTTPError:
        pass
    return appids


def fetch_app_details(appid: int) -> dict | None:
    url = f"{STEAM_STORE_URL}/appdetails"
    params = {"appids": appid, "l": "en"}
    try:
        response = httpx.get(url, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        app_data = data.get(str(appid), {})
        return app_data.get("data") if app_data.get("success") else None
    except httpx.HTTPError:
        return None


def build_chunk(appid: int, detail: dict) -> tuple[str, dict, str]:
    name = detail.get("name", f"Game {appid}")
    tags = [g["description"] for g in detail.get("genres", [])]
    categories = [c["description"] for c in detail.get("categories", [])]
    description = detail.get("short_description", "")
    metacritic = detail.get("metacritic", {}).get("score", "N/A")

    text = (
        f"Game: {name}\n"
        f"Description: {description}\n"
        f"Tags: {', '.join(tags)}\n"
        f"Categories: {', '.join(categories)}\n"
        f"Metacritic: {metacritic}"
    )

    metadata = {
        "name": name,
        "tags": tags or ["none"],
        "categories": categories or ["none"],
        "metacritic": metacritic,
    }

    return str(appid), metadata, text


# ── local cache ───────────────────────────────────────────────────────

def save_cache(records: list[dict]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(records)} games to {CACHE_PATH.name}")


def load_cache() -> list[dict]:
    if not CACHE_PATH.exists():
        print(f"  No cache found at {CACHE_PATH}. Run without --from-cache first.")
        return []
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ── ingest modes ──────────────────────────────────────────────────────

def ingest_from_cache():
    """Rebuild collection from local JSON cache — no API calls needed."""
    records = load_cache()
    if not records:
        return

    client = _get_client()
    existing = [c.name for c in client.list_collections()]
    if "games" in existing:
        client.delete_collection("games")

    collection = get_games_collection()
    ids_list = []
    metadatas_list = []
    documents_list = []

    for rec in records:
        doc_id, metadata, text = build_chunk(rec["appid"], rec["detail"])
        ids_list.append(doc_id)
        metadatas_list.append(metadata)
        documents_list.append(text)

    if ids_list:
        embeddings = embed(documents_list)
        collection.add(
            ids=ids_list,
            embeddings=embeddings,
            metadatas=metadatas_list,
            documents=documents_list,
        )

    print(f"Cache rebuild complete: {len(ids_list)} games indexed.")


def ingest_full(appids: list[int]):
    client = _get_client()
    existing = [c.name for c in client.list_collections()]
    if "games" in existing:
        client.delete_collection("games")

    collection = get_games_collection()
    ids_list = []
    metadatas_list = []
    documents_list = []
    cache_records: list[dict] = []

    for i, appid in enumerate(appids):
        detail = fetch_app_details(appid)
        if detail is None or detail.get("type") != "game":
            continue

        doc_id, metadata, text = build_chunk(appid, detail)
        ids_list.append(doc_id)
        metadatas_list.append(metadata)
        documents_list.append(text)
        cache_records.append({"appid": appid, "detail": detail})

        if (i + 1) % 10 == 0:
            print(f"  Fetched {i + 1}/{len(appids)} games...")
            time.sleep(0.5)

    if ids_list:
        embeddings = embed(documents_list)
        collection.add(
            ids=ids_list,
            embeddings=embeddings,
            metadatas=metadatas_list,
            documents=documents_list,
        )

    save_cache(cache_records)
    print(f"Full rebuild complete: {len(ids_list)} games indexed.")


def ingest_append(appids: list[int]):
    collection = get_games_collection()
    existing_ids = set(collection.get()["ids"])

    # Also load existing cache to append to it
    cache_records = load_cache() if CACHE_PATH.exists() else []

    new_ids = []
    new_metadatas = []
    new_documents = []

    for i, appid in enumerate(appids):
        if str(appid) in existing_ids:
            continue

        detail = fetch_app_details(appid)
        if detail is None or detail.get("type") != "game":
            continue

        doc_id, metadata, text = build_chunk(appid, detail)
        new_ids.append(doc_id)
        new_metadatas.append(metadata)
        new_documents.append(text)
        cache_records.append({"appid": appid, "detail": detail})

        if (i + 1) % 10 == 0:
            print(f"  Fetched {i + 1} new appids...")
            time.sleep(0.5)

    if new_ids:
        embeddings = embed(new_documents)
        collection.add(
            ids=new_ids,
            embeddings=embeddings,
            metadatas=new_metadatas,
            documents=new_documents,
        )
        save_cache(cache_records)

    print(f"Append complete: {len(new_ids)} new games added.")


# ── main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Steam game knowledge base ingestion")
    parser.add_argument(
        "--mode", choices=["full", "append"], default="full",
        help="Ingestion mode: full rebuild or append new games",
    )
    parser.add_argument(
        "--count", type=int, default=250,
        help="Number of top games to fetch (default: 250)",
    )
    parser.add_argument(
        "--from-cache", action="store_true",
        help="Rebuild from local cache instead of calling Steam API. "
             "Use this when switching embedding models — no API calls, just re-embed.",
    )
    args = parser.parse_args()

    if args.from_cache:
        print("Rebuilding from local cache (no API calls)...")
        ingest_from_cache()
        return

    print(f"Fetching top {args.count} games from Steam Charts...")
    appids = fetch_top_appids(args.count)
    if not appids:
        print("No appids found from Steam Charts, loading from local app list...")
        appids = _load_fallback_appids(args.count)

    print(f"Starting ingestion (mode={args.mode}) for {len(appids)} games...")

    if args.mode == "full":
        ingest_full(appids)
    else:
        ingest_append(appids)


def _load_fallback_appids(count: int) -> list[int]:
    try:
        response = httpx.get(STEAM_APP_LIST_URL, timeout=30.0)
        response.raise_for_status()
        data = response.json()
        apps = data.get("response", {}).get("apps", [])
        return [a["appid"] for a in apps[:count]]
    except httpx.HTTPError:
        print("Failed to fetch app list from Steam API.")
        return []


if __name__ == "__main__":
    main()
