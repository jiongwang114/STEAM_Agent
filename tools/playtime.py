import json
import urllib.request
from urllib.parse import urlencode

from config import STEAM_API_KEY, STEAM_API_URL


def get_user_playtime(steam_id: str = "", count: int = 10) -> dict:
    """
    读取指定 Steam 用户的游戏库和游玩时长。

    适用场景：
    - 用户明确要求根据自己的 Steam 游戏库进行推荐；
    - 用户要求参考已拥有、最常玩或最近游玩的游戏；
    - 需要先了解用户游戏库，再进行个性化相似游戏检索。

    不适用场景：
    - 普通游戏推荐；
    - 查询某款游戏的当前价格、折扣或商店评分；
    - 查询游戏类型、标签或相似度；
    - 用户没有提供有效 Steam ID 的情况。

    参数说明：
    - steam_id：当前用户已绑定的 17 位数字 Steam ID。不得猜测、替换或使用其他用户的 ID。
    - count：返回的主要游戏数量，范围为 1 到 50，默认返回 10 个。

    返回内容：
    - games：按累计游玩时长从高到低排列的游戏列表；
    - total_game_count：Steam 返回的游戏总数；
    - 每个游戏通常包含 appid、name、playtime_forever、playtime_2weeks 和图标字段。

    重要限制：
    - 游玩时长以 Steam 返回的分钟数为准，不要自行换算或推断用户喜好。
    - 最近游玩数据可能为空；这不代表用户从未游玩过这些游戏。
    - 本工具只提供游戏库和游玩数据，不直接产生推荐结论。
    """
    if not steam_id or not steam_id.isdigit() or len(steam_id) != 17:
        return {"error": "invalid_steam_id"}
    count = max(1, min(int(count), 50))
    params = {
        "key": STEAM_API_KEY,
        "steamid": steam_id,
        "format": "json",
        "include_appinfo": "true",
        "include_played_free_games": "true",
    }
    qs = urlencode(params)
    url = f"{STEAM_API_URL}/IPlayerService/GetOwnedGames/v0001/?{qs}"

    with urllib.request.urlopen(url, timeout=8.0) as resp:
        data = json.loads(resp.read())

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
    qs = urlencode(params)
    url = f"{STEAM_API_URL}/IPlayerService/GetRecentlyPlayedGames/v0001/?{qs}"

    try:
        with urllib.request.urlopen(url, timeout=5.0) as resp:
            data = json.loads(resp.read())
    except Exception:
        return {}

    games = data.get("response", {}).get("games", [])
    return {g["appid"]: g.get("playtime_2weeks", 0) for g in games}
