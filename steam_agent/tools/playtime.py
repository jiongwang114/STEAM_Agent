import json
import urllib.request

from ..config import STEAM_API_KEY, STEAM_API_URL


def get_user_playtime(steam_id: str, count: int = 10) -> dict:
    """
    Get a Steam user's owned games with playtime, sorted by total playtime descending.
    """
    params = {
        "key": STEAM_API_KEY,
        "steamid": steam_id,
        "format": "json",
        "include_appinfo": "true",
        "include_played_free_games": "true",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{STEAM_API_URL}/IPlayerService/GetOwnedGames/v0001/?{qs}"

    try:
        with urllib.request.urlopen(url, timeout=15.0) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        return {"error": str(exc)}

    games_raw = data.get("response", {}).get("games", [])
    if not games_raw:
        return {"games": [], "total_game_count": 0}

    games_raw.sort(key=lambda g: g.get("playtime_forever", 0), reverse=True)
    top_games = games_raw[:count]

    recently_played = _get_recently_played(steam_id)

    games = []
    for g in top_games:
        appid = g["appid"]
        games.append({
            "appid": appid,
            "name": g.get("name", "Unknown"),
            "playtime_forever": g.get("playtime_forever", 0),
            "playtime_2weeks": recently_played.get(appid, 0),
            "img_icon_url": g.get("img_icon_url", ""),
        })

    return {
        "games": games,
        "total_game_count": data["response"].get("game_count", len(games_raw)),
    }


def _get_recently_played(steam_id: str) -> dict[int, int]:
    """Returns a dict of appid -> playtime_2weeks."""
    params = {
        "key": STEAM_API_KEY,
        "steamid": steam_id,
        "format": "json",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{STEAM_API_URL}/IPlayerService/GetRecentlyPlayedGames/v0001/?{qs}"

    try:
        with urllib.request.urlopen(url, timeout=10.0) as resp:
            data = json.loads(resp.read())
    except Exception:
        return {}

    games = data.get("response", {}).get("games", [])
    return {g["appid"]: g.get("playtime_2weeks", 0) for g in games}
