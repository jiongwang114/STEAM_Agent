from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from config import (
    TOOL_CIRCUIT_COOLDOWN_SECONDS,
    TOOL_CIRCUIT_FAILURE_THRESHOLD,
    TOOL_MAX_RETRIES,
    TOOL_RESULT_MAX_CHARS,
    TOOL_TIMEOUT_SECONDS,
)
from tools.contracts import ToolError, ToolResult, ToolStatus, exception_result, normalize_tool_result


_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="agent-tool")
_BATCH_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-batch")
_RETRYABLE_STATUSES = {
    ToolStatus.TIMEOUT,
    ToolStatus.RATE_LIMITED,
    ToolStatus.UPSTREAM_ERROR,
}


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float = 0.0


class ToolCircuitBreaker:
    def __init__(self, failure_threshold: int, cooldown_seconds: float):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._states: dict[str, _CircuitState] = {}
        self._lock = threading.Lock()

    def allow(self, tool_name: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            state = self._states.setdefault(tool_name, _CircuitState())
            if not state.opened_at:
                return True
            if now - state.opened_at >= self.cooldown_seconds:
                state.failures = 0
                state.opened_at = 0.0
                return True
            return False

    def record(self, tool_name: str, success: bool, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            state = self._states.setdefault(tool_name, _CircuitState())
            if success:
                state.failures = 0
                state.opened_at = 0.0
                return
            state.failures += 1
            if state.failures >= self.failure_threshold:
                state.opened_at = now


_CIRCUITS = ToolCircuitBreaker(
    TOOL_CIRCUIT_FAILURE_THRESHOLD,
    TOOL_CIRCUIT_COOLDOWN_SECONDS,
)


def execute_tool(
    tool_name: str,
    function: Callable[..., Any],
    arguments: dict[str, Any],
    *,
    deadline_at: float | None = None,
    timeout_seconds: float = TOOL_TIMEOUT_SECONDS,
    max_retries: int = TOOL_MAX_RETRIES,
    circuit_breaker: ToolCircuitBreaker = _CIRCUITS,
) -> ToolResult:
    if not circuit_breaker.allow(tool_name):
        return ToolResult(
            status=ToolStatus.UPSTREAM_ERROR,
            error=ToolError(
                code="circuit_open",
                message=f"{tool_name} is temporarily unavailable after repeated failures.",
                retryable=False,
            ),
            meta={"tool": tool_name, "attempts": 0, "duration_seconds": 0.0},
        )

    started = time.perf_counter()
    result: ToolResult | None = None
    attempts = 0
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        remaining = _remaining_seconds(deadline_at)
        wait_seconds = min(timeout_seconds, remaining) if remaining is not None else timeout_seconds
        if wait_seconds <= 0:
            result = _timeout_result("Agent deadline reached before tool execution.")
        else:
            future = _POOL.submit(function, **arguments)
            try:
                value = future.result(timeout=wait_seconds)
                result = normalize_tool_result(tool_name, value)
            except concurrent.futures.TimeoutError:
                future.cancel()
                result = _timeout_result(f"{tool_name} exceeded {wait_seconds:.2f}s timeout.")
            except Exception as exc:
                result = exception_result(exc)

        if not _should_retry(result, attempt, max_retries, deadline_at):
            break
        # Back off transient upstream failures so retries do not amplify an outage.
        backoff = min(2.0 ** attempt, 8.0)
        remaining = _remaining_seconds(deadline_at)
        if remaining is not None:
            backoff = min(backoff, remaining)
        if backoff > 0:
            time.sleep(backoff)

    assert result is not None
    result = _limit_result(result, TOOL_RESULT_MAX_CHARS)
    success = result.status in {ToolStatus.SUCCESS, ToolStatus.EMPTY, ToolStatus.INVALID_INPUT}
    circuit_breaker.record(tool_name, success=success)
    result.meta = {
        **result.meta,
        "tool": tool_name,
        "attempts": attempts,
        "duration_seconds": round(time.perf_counter() - started, 6),
    }
    return result


def execute_tool_batch(requests: list[dict[str, Any]]) -> list[ToolResult]:
    """Execute policy-approved independent calls concurrently, preserving order."""
    futures = [
        _BATCH_POOL.submit(
            execute_tool,
            request["tool_name"],
            request["function"],
            request["arguments"],
            deadline_at=request.get("deadline_at"),
        )
        for request in requests
    ]
    return [future.result() for future in futures]


def _remaining_seconds(deadline_at: float | None) -> float | None:
    if deadline_at is None:
        return None
    return max(0.0, float(deadline_at) - time.time())


def _should_retry(
    result: ToolResult,
    attempt: int,
    max_retries: int,
    deadline_at: float | None,
) -> bool:
    if attempt >= max_retries or result.status not in _RETRYABLE_STATUSES:
        return False
    if result.error and not result.error.retryable:
        return False
    remaining = _remaining_seconds(deadline_at)
    return remaining is None or remaining > 0


def _timeout_result(message: str) -> ToolResult:
    return ToolResult(
        status=ToolStatus.TIMEOUT,
        error=ToolError(code="timeout", message=message, retryable=True),
    )


def _limit_result(result: ToolResult, max_chars: int) -> ToolResult:
    if len(result.model_dump_json(exclude_none=True)) <= max_chars:
        return result
    limited = result.model_copy(deep=True)
    for string_limit, list_limit in ((2000, 10), (1000, 8), (500, 5), (200, 3)):
        limited.data = _compact_value(result.data, string_limit, list_limit)
        limited.evidence = result.evidence[:list_limit]
        for item in limited.evidence:
            item.payload = _compact_value(item.payload, string_limit, list_limit)
        limited.meta = {**limited.meta, "result_truncated": True}
        if len(limited.model_dump_json(exclude_none=True)) <= max_chars:
            return limited
    limited.data = {"truncated": True, "message": "Tool result exceeded size budget."}
    limited.evidence = []
    return limited


def _compact_value(value: Any, string_limit: int, list_limit: int, depth: int = 0) -> Any:
    if depth >= 5:
        return "[nested value truncated]"
    if isinstance(value, str):
        return value[:string_limit]
    if isinstance(value, list):
        return [
            _compact_value(item, string_limit, list_limit, depth + 1)
            for item in value[:list_limit]
        ]
    if isinstance(value, dict):
        return {
            str(key)[:100]: _compact_value(item, string_limit, list_limit, depth + 1)
            for key, item in list(value.items())[:30]
        }
    return value
