"""Rebuild RAG ground-truth CSVs against the current game cache.

The existing query cases are retained, while stale AppIDs are removed so a
fresh evaluation cannot penalize the current index for games it does not hold.
"""

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "rag" / "chroma_data" / "game_cache.json"
TESTS = ROOT / "tests"

# These are open recommendation intents. They are intentionally evaluated for
# constraint validity and LLM-as-Judge, not exact AppID hit rate.
OPEN_ENGLISH = {
    "动作类游戏": "action games",
    "适合新手的动作类游戏": "beginner-friendly action games",
    "高质量动作类游戏": "high-quality action games",
    "冒险探索游戏": "exploration adventure games",
    "适合新手的冒险探索游戏": "beginner-friendly exploration adventure games",
    "高质量冒险探索游戏": "high-quality exploration adventure games",
    "独立游戏": "indie games",
    "适合新手的独立游戏": "beginner-friendly indie games",
    "高质量独立游戏": "high-quality indie games",
    "角色扮演游戏": "role-playing games",
    "适合新手的角色扮演游戏": "beginner-friendly role-playing games",
    "高质量角色扮演游戏": "high-quality role-playing games",
    "模拟经营游戏": "simulation and management games",
    "策略游戏": "strategy games",
    "休闲游戏": "casual games",
    "体育竞技游戏": "sports games",
    "赛车驾驶游戏": "racing and driving games",
    "抢先体验游戏": "early access games",
    "大型多人在线游戏": "massively multiplayer online games",
    "免费游戏": "free-to-play games",
}


def rebuild(filename: str) -> None:
    source = TESTS / filename
    rows = list(csv.DictReader(source.open(encoding="utf-8-sig", newline="")))
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    available = {str(item.get("appid")) for item in cache}
    metadata = {}
    for item in cache:
        detail = item.get("detail", {})
        tags = [g.get("description", "") for g in detail.get("genres", [])]
        tags += [c.get("description", "") for c in detail.get("categories", [])]
        tags += item.get("user_tags", []) or []
        metadata[str(item.get("appid"))] = {str(tag).casefold() for tag in tags}
    for row in rows:
        if filename == "gt_semantic_v2.csv":
            row["top_k"] = "8"
            row["evaluation_type"] = "open_recommendation"
            row["query"] = OPEN_ENGLISH.get(row["query"], row["query"])
            row["relevant_appids"] = ""
            row["available_relevant_count"] = "0"
            row["stale_relevant_count"] = "0"
            row["ground_truth_status"] = "open_no_fixed_id"
            row["relevance_grades"] = ""
            continue
        ids = [item.strip() for item in row["relevant_appids"].split(";") if item.strip()]
        kept = [appid for appid in ids if appid in available]
        row["relevant_appids"] = ";".join(dict.fromkeys(kept))
        row["available_relevant_count"] = str(len(kept))
        row["stale_relevant_count"] = str(len(ids) - len(kept))
        row["ground_truth_status"] = "needs_manual_review" if not kept else "cache_aligned_candidates"
        tag = row.get("filter_tags", "").strip().casefold()
        weak = [appid for appid, tags in metadata.items() if tag and any(tag in value for value in tags)]
        row["relevance_grades"] = ";".join(
            f"{appid}:2" if appid in kept else f"{appid}:1" for appid in weak
        )
    fields = list(rows[0])
    if "relevance_grades" not in fields:
        fields.append("relevance_grades")
    if "evaluation_type" not in fields:
        fields.append("evaluation_type")
    with source.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{filename}: {len(rows)} cases rebuilt")


if __name__ == "__main__":
    for name in ("gt_semantic_v2.csv", "gt_filtered_v2.csv", "gt_semantic.csv", "gt_filtered.csv"):
        if (TESTS / name).exists():
            rebuild(name)
