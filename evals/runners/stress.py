from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from statistics import mean

from langchain_core.messages import AIMessage, HumanMessage

from steam_agent.api.routes import _get_thread_lock
from steam_agent.graph.run_context import compact_messages
from steam_agent.tools.executor import ToolCircuitBreaker, execute_tool, execute_tool_batch

from ..artifacts import write_companion_artifacts


async def run_stress_eval(output_path: Path) -> dict:
    rows = [
        _long_context_case(),
        await _same_thread_case(),
        await _different_thread_case(),
        _large_tool_result_case(),
        _parallel_tool_batch_case(),
    ]
    report = {
        "summary": {
            "cases": len(rows),
            "task_success": mean(float(row["passed"]) for row in rows),
        },
        "cases": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_companion_artifacts(report, output_path)
    return report


def _long_context_case() -> dict:
    messages = []
    for turn in range(50):
        messages.extend([
            HumanMessage(content=f"turn-{turn}:" + "偏好信息" * 80),
            AIMessage(content=f"answer-{turn}:" + "回答" * 40),
        ])
    compacted, stats = compact_messages(messages, max_tokens=6000)
    latest_preserved = any(
        str(getattr(message, "content", "")).startswith("turn-49:")
        for message in compacted
    )
    return {
        "case_id": "stress-long-context-50-turns",
        "passed": stats["estimated_tokens"] <= 6000 and stats["dropped_turns"] > 0 and latest_preserved,
        "estimated_tokens": stats["estimated_tokens"],
        "dropped_turns": stats["dropped_turns"],
        "messages_retained": len(compacted),
    }


async def _same_thread_case() -> dict:
    active = 0
    max_active = 0

    async def task():
        nonlocal active, max_active
        async with _get_thread_lock("stress-shared"):
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1

    started = time.perf_counter()
    await asyncio.gather(*(task() for _ in range(8)))
    return {
        "case_id": "stress-same-thread-serialization",
        "passed": max_active == 1,
        "max_concurrency": max_active,
        "duration_ms": (time.perf_counter() - started) * 1000,
    }


async def _different_thread_case() -> dict:
    active = 0
    max_active = 0

    async def task(index: int):
        nonlocal active, max_active
        async with _get_thread_lock(f"stress-independent-{index}"):
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1

    started = time.perf_counter()
    await asyncio.gather(*(task(index) for index in range(8)))
    duration_ms = (time.perf_counter() - started) * 1000
    return {
        "case_id": "stress-different-thread-parallelism",
        "passed": max_active >= 4 and duration_ms < 100,
        "max_concurrency": max_active,
        "duration_ms": duration_ms,
    }


def _large_tool_result_case() -> dict:
    result = execute_tool(
        "stress_large_result",
        lambda: {"results": [{"appid": index, "text": "x" * 5000} for index in range(100)]},
        {},
        timeout_seconds=1,
        max_retries=0,
        circuit_breaker=ToolCircuitBreaker(3, 30),
    )
    encoded = result.model_dump_json(exclude_none=True)
    return {
        "case_id": "stress-tool-result-budget",
        "passed": bool(result.meta.get("result_truncated")) and len(encoded) <= 12000,
        "serialized_chars": len(encoded),
        "result_truncated": bool(result.meta.get("result_truncated")),
    }


def _parallel_tool_batch_case() -> dict:
    def slow(value: int):
        time.sleep(0.04)
        return {"value": value}

    requests = [
        {"tool_name": f"slow_{index}", "function": slow, "arguments": {"value": index}}
        for index in range(3)
    ]
    started = time.perf_counter()
    results = execute_tool_batch(requests)
    duration_ms = (time.perf_counter() - started) * 1000
    serial_estimate_ms = 120.0
    return {
        "case_id": "stress-independent-tool-parallelism",
        "passed": len(results) == 3 and duration_ms < 90,
        "duration_ms": duration_ms,
        "estimated_speedup": serial_estimate_ms / duration_ms,
    }
