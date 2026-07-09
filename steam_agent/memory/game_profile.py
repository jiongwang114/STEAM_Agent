"""Game profile cache — 3-level: memory -> SQLite -> Steam API, 6-hour TTL.

One user profile summarizes: total game count, top genres by playtime,
top 5 games, and recent 2-week activity. ~300 tokens max.

Usage:
    from .game_profile import get_game_profile, init_game_profile_table
"""

import json
import sqlite3
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from ..config import STEAM_API_KEY, STEAM_API_URL, SQLITE_DB_PATH

CACHE_TTL = 6 * 3600  # 6 hours
GAME_CACHE_PATH = Path(__file__).resolve().parent.parent / "rag" / "chroma_data" / "game_cache.json"

# In-memory: steam_id -> (profile_text, unix_timestamp)
_memory_cache: dict[str, tuple[str, float]] = {}


def init_game_profile_table():
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_game_profile (
            steam_id TEXT PRIMARY KEY,
            profile TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def get_game_profile(steam_id: str) -> str:
    """
    Returns a Chinese game profile summary string for the System Prompt.
    Three-level fallback: memory -> SQLite -> Steam API.

    Returns empty string if steam_id is empty or all levels fail.
    """
    if not steam_id or not STEAM_API_KEY:
        return ""

    # Level 1: memory
    if steam_id in _memory_cache:
        text, ts = _memory_cache[steam_id]
        if time.time() - ts < CACHE_TTL:
            return text

    # Level 2: SQLite
    try:
        conn = sqlite3.connect(SQLITE_DB_PATH)
        row = conn.execute(
            "SELECT profile, updated_at FROM user_game_profile WHERE steam_id = ?",
            (steam_id,),
        ).fetchone()
        conn.close()

        if row and time.time() - row[1] < CACHE_TTL:
            _memory_cache[steam_id] = (row[0], row[1])
            return row[0]
    except Exception:
        pass

    # Level 3: Steam API
    try:
        profile = _fetch_and_build(steam_id)
        if not profile:
            return ""

        now = time.time()
        _memory_cache[steam_id] = (profile, now)

        conn = sqlite3.connect(SQLITE_DB_PATH)
        conn.execute(
            "INSERT OR REPLACE INTO user_game_profile (steam_id, profile, updated_at) "
            "VALUES (?, ?, ?)",
            (steam_id, profile, now),
        )
        conn.commit()
        conn.close()

        return profile
    except Exception:
        return ""


def _fetch_and_build(steam_id: str) -> str:
    """Call Steam API, cross-reference genres, build summary."""
    # Get all owned games (sorted by playtime)
    params = {
        "key": STEAM_API_KEY,
        "steamid": steam_id,
        "format": "json",
        "include_appinfo": "true",
        "include_played_free_games": "true",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{STEAM_API_URL}/IPlayerService/GetOwnedGames/v0001/?{qs}"

    with urllib.request.urlopen(url, timeout=15.0) as resp:
        data = json.loads(resp.read())

    games_raw = data.get("response", {}).get("games", [])
    if not games_raw:
        return ""

    total = data["response"].get("game_count", len(games_raw))

    # Sort by playtime descending
    games_raw.sort(key=lambda g: g.get("playtime_forever", 0), reverse=True)

    # Recently played
    recent = _get_recently_played(steam_id)

    # Load genre cache
    genre_map = _load_genre_cache()

    # Genre aggregation (all games, not just top)
    genre_hours: dict[str, int] = defaultdict(int)
    for g in games_raw:
        h = g.get("playtime_forever", 0)
        if h > 0:
            genres = genre_map.get(str(g.get("appid", "")), [])
            for gen in genres:
                genre_hours[gen] += int(h)

    top_genres = sorted(genre_hours.items(), key=lambda x: x[1], reverse=True)[:5]
    genre_line = "、".join(f"{g}({int(h/60)}h)" for g, h in top_genres if h >= 60)

    # Top 5 games
    top5 = []
    for g in games_raw[:5]:
        h = g.get("playtime_forever", 0)
        h2 = recent.get(g.get("appid", 0), 0)
        name = g.get("name", "Unknown")
        if h2 > 0:
            top5.append(f"{name}({int(h/60)}h,近两周{int(h2/60)}h)")
        else:
            top5.append(f"{name}({int(h/60)}h)")

    # Recent activity summary
    recent_active = []
    for g in games_raw:
        appid = g.get("appid", 0)
        h2 = recent.get(appid, 0)
        if h2 > 60:
            recent_active.append(f"{g.get('name','?')}({int(h2/60)}h)")

    # Build profile
    lines = [f"- 游戏总数: {total} 款"]
    if genre_line:
        lines.append(f"- 最常玩类型: {genre_line}")
    lines.append(f"- Top 5 游戏: {' | '.join(top5)}")
    if recent_active:
        lines.append(f"- 近两周活跃: {'、'.join(recent_active[:5])}")

    return "\n".join(lines)


def _get_recently_played(steam_id: str) -> dict[int, int]:
    params = {"key": STEAM_API_KEY, "steamid": steam_id, "format": "json"}
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{STEAM_API_URL}/IPlayerService/GetRecentlyPlayedGames/v0001/?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=10.0) as resp:
            data = json.loads(resp.read())
    except Exception:
        return {}
    games = data.get("response", {}).get("games", [])
    return {g["appid"]: g.get("playtime_2weeks", 0) for g in games}


def _load_genre_cache() -> dict[str, list[str]]:
    """Load appid -> [genre descriptions] from game_cache.json, lazy."""
    if not GAME_CACHE_PATH.exists():
        return {}
    with open(GAME_CACHE_PATH, encoding="utf-8") as f:
        games = json.load(f)

    result: dict[str, list[str]] = {}
    for g in games:
        appid = str(g.get("appid", ""))
        if not appid:
            continue
        detail = g.get("detail", {})
        genres = [x.get("description", "") for x in detail.get("genres", []) if x.get("description")]
        if genres:
            result[appid] = genres
    return result
