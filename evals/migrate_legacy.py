from __future__ import annotations

import csv
import json
import re
from pathlib import Path


ALL_TOOLS = [
    "get_user_playtime",
    "search_steam_store",
    "rag_search_similar_games",
    "save_user_insight",
    "recall_user_memory",
    "recall_message_detail",
]


def _tool_name(value: str) -> str:
    return re.split(r"[（(]", value.strip(), maxsplit=1)[0].strip()


def _parse_tools(expected: str, forbidden: str) -> dict:
    expected = (expected or "").strip()
    required: list[str] = []
    optional: list[str] = []
    ordered: list[str] = []
    if expected and expected != "无":
        if "→" in expected:
            ordered = [_tool_name(item) for item in expected.split("→")]
            required = list(ordered)
        elif "+" in expected:
            required = [_tool_name(item) for item in expected.split("+")]
        elif "可选" in expected:
            left, right = expected.split("可选", 1)
            required = [_tool_name(left.rstrip("(（ "))]
            optional = [_tool_name(right.rstrip(")） "))]
        else:
            required = [_tool_name(expected)]
    forbidden = (forbidden or "").strip()
    if forbidden == "全部":
        forbidden_tools = list(ALL_TOOLS)
    elif forbidden and forbidden != "无":
        forbidden_tools = [_tool_name(item) for item in re.split(r"[;；]", forbidden)]
    else:
        forbidden_tools = []
    return {
        "required": required,
        "optional": optional,
        "forbidden": forbidden_tools,
        "ordered": ordered,
    }


def migrate_agent(source: Path, target: Path) -> int:
    recommendation_categories = {"语义推荐", "约束过滤", "链式调用", "匿名用户", "商店查询"}
    with source.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            category = row.get("category") or row.get("类别") or "legacy"
            appids = [item for item in (row.get("relevant_appids") or "").split(";") if item]
            answer_type = "recommendation" if category in recommendation_categories else "answer"
            if "无结果" in (row.get("期望行为描述") or ""):
                answer_type = "clarification"
            payload = {
                "id": f"legacy-agent-{row['id']}",
                "category": category,
                "message": row["用户提问"],
                "steam_id": row.get("steam_id") or None,
                "fixture": f"legacy/{row['id']}",
                "tools": _parse_tools(row.get("预期调用的工具", ""), row.get("预期不调用的工具", "")),
                "answer": {
                    "answer_type": answer_type,
                    "required_appids": appids,
                    "require_grounding": answer_type == "recommendation",
                },
                "expected_termination": ["completed", "clarified"],
                "tags": ["legacy", "migration"],
                "provenance": str(source.as_posix()),
                "review_status": "needs_review",
                "notes": row.get("备注") or row.get("期望行为描述") or "",
            }
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(rows)


def migrate_retrieval(sources: list[Path], target: Path) -> int:
    rows: list[tuple[dict, str]] = []
    for source in sources:
        with source.open(newline="", encoding="utf-8-sig") as file:
            rows.extend((row, source.stem) for row in csv.DictReader(file))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as file:
        for index, (row, source_name) in enumerate(rows, start=1):
            appids = [item.strip() for item in row["relevant_appids"].split(";") if item.strip()]
            constraints = {}
            if (row.get("free_only") or "").strip() == "1":
                constraints["free_only"] = True
            if (row.get("min_year") or "").strip():
                constraints["min_year"] = int(row["min_year"])
            payload = {
                "id": f"legacy-rag-{index:03d}",
                "query": row["query"],
                "qrels": {appid: 2 for appid in appids},
                "split": "dev" if index <= 40 else "test",
                "top_k": int(row.get("top_k") or 10),
                "constraints": constraints,
                "category": "filtered" if source_name == "gt_filtered" else "semantic",
                "provenance": source_name,
                "review_status": "needs_review",
                "notes": row.get("notes") or "",
            }
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(rows)


def migrate_all(root: Path) -> dict[str, int]:
    legacy = root / "steam_agent" / "tests"
    datasets = root / "evals" / "datasets"
    return {
        "agent": migrate_agent(legacy / "eval_queries.csv", datasets / "agent_behavior.v1.jsonl"),
        "retrieval": migrate_retrieval(
            [legacy / "gt_semantic.csv", legacy / "gt_filtered.csv"],
            datasets / "retrieval.v1.jsonl",
        ),
    }
