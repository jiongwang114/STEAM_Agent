"""
Offline data ingestion script for the Steam game knowledge base.

Usage:
    python -m steam_agent.rag.ingest                     # fetch from API + rebuild (saves cache)
    python -m steam_agent.rag.ingest --from-cache          # rebuild from local cache (no API calls)
    python -m steam_agent.rag.ingest --mode append         # fetch new games only
"""

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..config import (
    EMBEDDING_MODEL,
    CHROMA_PERSIST_DIR,
    RAG_DEFAULT_GAME_COUNT,
    RERANKER_MODEL,
    STEAM_API_KEY,
    STEAM_STORE_URL,
)
from .embedder import embed
from .vector_store import _get_client, current_games_collection_name

STEAM_TOP_GAMES_URL = "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/"
STEAM_APP_LIST_URL = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"

DATA_DIR = Path(CHROMA_PERSIST_DIR)
CACHE_PATH = DATA_DIR / "game_cache.json"
INDEX_MANIFEST_PATH = DATA_DIR / "index_manifest.json"
CURRENT_INDEX_PATH = DATA_DIR / "current_index.json"
CHUNK_SCHEMA_VERSION = "game-document-v2"


def fetch_top_appids(count: int = RAG_DEFAULT_GAME_COUNT) -> list[int]:
    appids = []
    try:
        key = STEAM_API_KEY.strip()
        params = (
            {"key": key}
            if key and key.lower() not in {"test-key", "changeme", "your-key-here"}
            else {}
        )
        response = httpx.get(STEAM_TOP_GAMES_URL, params=params, timeout=15.0)
        response.raise_for_status()
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


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", " ", text).strip()


# Categories that describe HOW you play — high signal for search intent.
GAMEPLAY_CATEGORIES = {
    "Single-player", "Multi-player", "Co-op", "Online Co-op",
    "LAN Co-op", "Shared/Split Screen Co-op", "Shared/Split Screen",
    "PvP", "Online PvP", "MMO", "Cross-Platform Multiplayer",
}


def build_chunk(appid: int, detail: dict, user_tags: list[str] | None = None) -> tuple[str, dict, str]:
    name = detail.get("name", f"Game {appid}")
    description = detail.get("short_description", "")
    genres = [g["description"] for g in detail.get("genres", [])]
    all_categories = [c["description"] for c in detail.get("categories", [])]
    gameplay_modes = [c for c in all_categories if c in GAMEPLAY_CATEGORIES]
    metacritic = detail.get("metacritic", {}).get("score", "N/A")
    developers = ", ".join(detail.get("developers", []))
    release_year = detail.get("release_date", {}).get("date", "Unknown")[-4:]
    is_free = detail.get("is_free", False)

    # Prefer user_tags for filtering (much finer). Fall back to genres.
    filter_tags = (user_tags if user_tags else None) or genres or ["none"]

    # Text for embedding: everything semantic goes here.
    parts = [f"{name}. {description}"]
    if genres:
        parts.append(f"Genres: {', '.join(genres)}.")
    if user_tags:
        parts.append(f"User Tags: {', '.join(user_tags[:15])}.")
    if developers:
        parts.append(f"Developer: {developers}.")
    text = " ".join(parts)

    # Metadata: ONLY hard constraints (no genres, no user_tags).
    # Soft/semantic concepts ("horror", "pixel", "roguelike") belong in embedding, not filters.
    has_multiplayer = any(
        c in {"Multi-player", "Co-op", "Online Co-op", "PvP", "Online PvP", "MMO"}
        for c in all_categories
    )

    metadata = {
        "name": name,
        "developers": developers,
        "is_free": is_free,
        "release_year": int(release_year) if release_year.isdigit() else 0,
        "has_multiplayer": has_multiplayer,
        "metacritic": metacritic if isinstance(metacritic, int) else 0,
    }

    return str(appid), metadata, text


# ── local cache ───────────────────────────────────────────────────────

def save_cache(records: list[dict]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = CACHE_PATH.with_suffix(".json.tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, CACHE_PATH)
    print(f"  Saved {len(records)} games to {CACHE_PATH.name}")


def load_cache() -> list[dict]:
    if not CACHE_PATH.exists():
        print(f"  No cache found at {CACHE_PATH}. Run without --from-cache first.")
        return []
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def write_index_manifest(
    records: list[dict],
    *,
    index_version: str | None = None,
    collection_name: str | None = None,
    parent_version: str = "",
    failures: int = 0,
    started_at: str | None = None,
    duration_seconds: float = 0.0,
) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    index_version = index_version or "legacy"
    collection_name = collection_name or "games"
    documents = [
        build_chunk(record["appid"], record["detail"], record.get("user_tags"))[2]
        for record in records
    ]
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at or "",
        "duration_seconds": round(duration_seconds, 3),
        "index_version": index_version,
        "parent_version": parent_version,
        "collection": collection_name,
        "game_count": len(records),
        "failure_count": failures,
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "reranker_model": RERANKER_MODEL,
        "code_version": os.environ.get("GIT_SHA", "working-tree"),
        "cache_sha256": hashlib.sha256(CACHE_PATH.read_bytes()).hexdigest()
        if CACHE_PATH.exists() else "",
        "documents_sha256": hashlib.sha256(
            json.dumps(documents, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    temp_path = INDEX_MANIFEST_PATH.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, INDEX_MANIFEST_PATH)
    return manifest


def _switch_current_index(index_version: str, collection_name: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = CURRENT_INDEX_PATH.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps({
            "index_version": index_version,
            "collection": collection_name,
            "switched_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp_path, CURRENT_INDEX_PATH)


def _build_collection(
    records: list[dict],
    *,
    mode: str,
    failures: int = 0,
) -> tuple[str, str, int]:
    """Build a new collection and return (version, name, failure_count)."""
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    parent = {}
    try:
        current = current_games_collection_name()
        parent = {"collection": current}
    except Exception:
        pass
    index_version = f"games-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{os.getpid()}"
    collection_name = f"games_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.getpid()}"
    collection = _get_client().get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    ids_list: list[str] = []
    metadatas_list: list[dict] = []
    documents_list: list[str] = []
    seen: set[str] = set()
    for record in records:
        doc_id, metadata, text = build_chunk(
            record["appid"], record["detail"], record.get("user_tags")
        )
        if doc_id in seen:
            continue
        seen.add(doc_id)
        ids_list.append(doc_id)
        metadatas_list.append(metadata)
        documents_list.append(text)
    if ids_list:
        collection.add(
            ids=ids_list,
            embeddings=embed(documents_list),
            metadatas=metadatas_list,
            documents=documents_list,
        )
    if collection.count() != len(ids_list):
        raise RuntimeError(f"index validation failed: expected {len(ids_list)}, got {collection.count()}")
    save_cache(records)
    write_index_manifest(
        records,
        index_version=index_version,
        collection_name=collection_name,
        parent_version=str(parent.get("collection", "")),
        failures=failures,
        started_at=started_at,
        duration_seconds=time.perf_counter() - started,
    )
    _switch_current_index(index_version, collection_name)
    return index_version, collection_name, 0


# ── ingest modes ──────────────────────────────────────────────────────

def ingest_from_cache():
    """Rebuild collection from local JSON cache — no API calls needed."""
    records = load_cache()
    if not records:
        return

    version, collection, _ = _build_collection(records, mode="cache")
    print(f"Cache rebuild complete: version={version}, collection={collection}.")


def ingest_full(appids: list[int]):
    cache_records: list[dict] = []
    failures = 0

    for i, appid in enumerate(appids):
        detail = fetch_app_details(appid)
        if detail is None or detail.get("type") != "game":
            failures += 1
            continue
        cache_records.append({"appid": appid, "detail": detail})

        if (i + 1) % 10 == 0:
            print(f"  Fetched {i + 1}/{len(appids)} games...")
            time.sleep(0.5)

    version, collection, _ = _build_collection(
        cache_records,
        mode="full",
        failures=failures,
    )
    print(f"Full rebuild complete: version={version}, collection={collection}, failures={failures}.")


def ingest_append(appids: list[int]):
    # Also load existing cache to append to it
    cache_records = load_cache() if CACHE_PATH.exists() else []
    existing_ids = {str(record.get("appid")) for record in cache_records}
    new_count = 0

    for i, appid in enumerate(appids):
        if str(appid) in existing_ids:
            continue

        detail = fetch_app_details(appid)
        if detail is None or detail.get("type") != "game":
            continue

        cache_records.append({"appid": appid, "detail": detail})
        new_count += 1

        if (i + 1) % 10 == 0:
            print(f"  Fetched {i + 1} new appids...")
            time.sleep(0.5)

    if new_count:
        version, collection, _ = _build_collection(cache_records, mode="append")
        print(f"Append complete: {new_count} new games added, version={version}, collection={collection}.")
    else:
        print("Append complete: 0 new games added.")


# ── main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Steam game knowledge base ingestion")
    parser.add_argument(
        "--mode", choices=["full", "append"], default="full",
        help="Ingestion mode: full rebuild or append new games",
    )
    parser.add_argument(
        "--count", type=int, default=RAG_DEFAULT_GAME_COUNT,
        help=f"Number of top games to fetch (default: {RAG_DEFAULT_GAME_COUNT})",
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
