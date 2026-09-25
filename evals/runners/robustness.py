from __future__ import annotations

import time
import urllib.error
from pathlib import Path
from statistics import mean

from steam_agent.tools.executor import ToolCircuitBreaker, execute_tool

from ..models import RobustnessCase, load_jsonl


def run_robustness_eval(dataset_path: Path) -> dict:
    cases = load_jsonl(dataset_path, RobustnessCase)
    rows = []
    for case in cases:
        outcomes = list(case.outcomes)
        function_calls = 0

        def injected_tool():
            nonlocal function_calls
            function_calls += 1
            outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
            return _produce(outcome, case.timeout_ms)

        circuit = ToolCircuitBreaker(case.circuit_threshold, cooldown_seconds=30)
        result = None
        started = time.perf_counter()
        for _ in range(case.invocations):
            result = execute_tool(
                "injected_tool",
                injected_tool,
                {},
                deadline_at=time.time() - 1 if case.deadline_expired else None,
                timeout_seconds=case.timeout_ms / 1000,
                max_retries=case.max_retries,
                circuit_breaker=circuit,
            )
        duration_ms = (time.perf_counter() - started) * 1000
        assert result is not None
        error_code = result.error.code if result.error else ""
        passed = (
            result.status.value == case.expected_status
            and error_code == case.expected_error_code
            and (case.expected_attempts is None or result.meta.get("attempts") == case.expected_attempts)
            and (case.expected_function_calls is None or function_calls == case.expected_function_calls)
            and duration_ms <= case.max_duration_ms
        )
        rows.append({
            "case_id": case.id,
            "passed": passed,
            "status": result.status.value,
            "error_code": error_code,
            "attempts": result.meta.get("attempts"),
            "function_calls": function_calls,
            "duration_ms": duration_ms,
        })
    return {
        "summary": {
            "cases": len(rows),
            "task_success": mean(float(row["passed"]) for row in rows) if rows else 0.0,
            "max_duration_ms": max((row["duration_ms"] for row in rows), default=0.0),
        },
        "cases": rows,
    }


def _produce(outcome: str, timeout_ms: int):
    if outcome == "success":
        return {"results": [{"appid": 10}]}
    if outcome == "empty":
        return {"results": []}
    if outcome == "timeout":
        time.sleep(timeout_ms / 1000 * 4)
        return {"results": []}
    if outcome == "connection_error":
        raise ConnectionError("injected connection failure")
    if outcome == "rate_limited":
        raise urllib.error.HTTPError("https://example.invalid", 429, "rate limited", {}, None)
    if outcome == "invalid_input":
        raise ValueError("injected invalid input")
    raise RuntimeError("injected upstream failure")
