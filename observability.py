"""Lightweight in-process observability for the API and Agent runtime.

The registry intentionally has no external dependency. It gives local and demo
deployments useful metrics while LangSmith remains the detailed trace backend.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


_request_id: ContextVar[str] = ContextVar("request_id", default="")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str | None = None):
    request_id = value if value and _REQUEST_ID_PATTERN.fullmatch(value) else uuid.uuid4().hex
    return request_id, _request_id.set(request_id)


def reset_request_id(token) -> None:
    _request_id.reset(token)


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line, enriched with request correlation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def _key(name: str, labels: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return name, tuple(sorted((key, str(value)) for key, value in labels.items()))


@dataclass
class _Histogram:
    max_samples: int
    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    samples: deque[float] = field(init=False)

    def __post_init__(self) -> None:
        self.samples = deque(maxlen=self.max_samples)

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        self.samples.append(value)

    def snapshot(self) -> dict[str, float | int]:
        ordered = sorted(self.samples)

        def percentile(fraction: float) -> float:
            if not ordered:
                return 0.0
            index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
            return round(ordered[index], 6)

        return {
            "count": self.count,
            "sum": round(self.total, 6),
            "min": round(self.minimum or 0.0, 6),
            "max": round(self.maximum or 0.0, 6),
            "avg": round(self.total / self.count, 6) if self.count else 0.0,
            "p50": percentile(0.50),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
        }


class MetricsRegistry:
    """Thread-safe counters and bounded latency histograms."""

    def __init__(self, max_samples: int = 1000) -> None:
        self._max_samples = max_samples
        self._started_at = time.time()
        self._lock = threading.Lock()
        self._counters: dict[tuple, float] = defaultdict(float)
        self._histograms: dict[tuple, _Histogram] = {}

    def increment(self, name: str, value: float = 1, **labels: str) -> None:
        with self._lock:
            self._counters[_key(name, labels)] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = _key(name, labels)
        with self._lock:
            histogram = self._histograms.setdefault(key, _Histogram(self._max_samples))
            histogram.observe(float(value))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in sorted(self._counters.items())
            ]
            histograms = [
                {"name": name, "labels": dict(labels), **histogram.snapshot()}
                for (name, labels), histogram in sorted(self._histograms.items())
            ]
        return {
            "uptime_seconds": round(time.time() - self._started_at, 3),
            "counters": counters,
            "histograms": histograms,
        }

    def reset(self) -> None:
        """Clear values; intended for isolated tests."""
        with self._lock:
            self._started_at = time.time()
            self._counters.clear()
            self._histograms.clear()


metrics = MetricsRegistry()


def record_agent_run(
    *,
    mode: str,
    status: str,
    duration_seconds: float,
    tool_calls: list[str] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    ttft_seconds: float | None = None,
) -> None:
    """Record the common metrics produced by a completed Agent invocation."""
    metrics.increment("agent_requests_total", mode=mode, status=status)
    metrics.observe("agent_duration_seconds", duration_seconds, mode=mode, status=status)
    metrics.increment("agent_input_tokens_total", input_tokens, mode=mode)
    metrics.increment("agent_output_tokens_total", output_tokens, mode=mode)
    if ttft_seconds is not None:
        metrics.observe("agent_ttft_seconds", ttft_seconds, mode=mode)
    for tool_name in tool_calls or []:
        metrics.increment("agent_tool_calls_total", tool=tool_name)


def record_tool_execution(*, tool: str, status: str, duration_seconds: float) -> None:
    metrics.increment("agent_tool_executions_total", tool=tool, status=status)
    metrics.observe(
        "agent_tool_duration_seconds",
        duration_seconds,
        tool=tool,
        status=status,
    )
