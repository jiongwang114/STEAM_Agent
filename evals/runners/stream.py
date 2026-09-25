from __future__ import annotations

import json
import time
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from steam_agent.graph.builder import _compile
from steam_agent.graph.nodes import get_tool_map
from steam_agent.graph.run_context import new_run_context
from steam_agent.tools.registry import FixtureToolRegistry

from ..artifacts import write_companion_artifacts
from ..metrics import percentile
from ..models import AgentCase, load_jsonl
from ..reporting import build_manifest


async def run_stream_latency_eval(*, root: Path, dataset_path: Path, output_path: Path, limit: int = 5) -> dict:
    cases = [case for case in load_jsonl(dataset_path, AgentCase) if case.review_status == "reviewed"][:limit]
    rows = []
    for case in cases:
        fixture = FixtureToolRegistry.from_path(root / "evals" / "fixtures" / f"{case.fixture}.json")
        graph = _compile(MemorySaver())
        state = {
            "messages": [HumanMessage(content=case.message)],
            "steam_id": case.steam_id,
            "user_id": case.user_id,
            **new_run_context(),
        }
        config = {"configurable": {
            "thread_id": f"stream_{case.id}",
            "tool_map": fixture.as_tool_map(list(get_tool_map())),
        }}
        started = time.perf_counter()
        first_token_at = None
        visible_characters = 0
        async for event in graph.astream_events(state, config, version="v2"):
            if event.get("event") != "on_chat_model_stream":
                continue
            if event.get("metadata", {}).get("langgraph_node") == "guard":
                continue
            chunk = event.get("data", {}).get("chunk")
            content = getattr(chunk, "content", "") if chunk else ""
            if content:
                first_token_at = first_token_at or time.perf_counter()
                visible_characters += len(content)
        ended = time.perf_counter()
        rows.append({
            "case_id": case.id,
            "passed": first_token_at is not None and visible_characters > 0,
            "ttft_seconds": first_token_at - started if first_token_at else 0.0,
            "total_seconds": ended - started,
            "visible_characters": visible_characters,
        })
        _save(_report(root, dataset_path, rows), output_path)
    report = _report(root, dataset_path, rows)
    _save(report, output_path)
    return report


def _report(root: Path, dataset_path: Path, rows: list[dict]) -> dict:
    ttft = [row["ttft_seconds"] for row in rows]
    total = [row["total_seconds"] for row in rows]
    manifest = build_manifest(
        root=root,
        model="deepseek-chat-stream",
        model_parameters={},
        dataset_paths=[dataset_path],
        repeats=1,
        label="stream-latency",
    )
    return {
        "manifest": manifest.__dict__,
        "summary": {
            "cases": len(rows),
            "task_success": sum(row["passed"] for row in rows) / len(rows) if rows else 0.0,
            "ttft_p50_seconds": percentile(ttft, 50),
            "ttft_p95_seconds": percentile(ttft, 95),
            "total_p50_seconds": percentile(total, 50),
            "total_p95_seconds": percentile(total, 95),
        },
        "cases": rows,
    }


def _save(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_companion_artifacts(report, path)
