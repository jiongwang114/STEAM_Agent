from __future__ import annotations

from enum import Enum
import socket
from typing import Any
import urllib.error

from pydantic import BaseModel, Field


class ToolStatus(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    INVALID_INPUT = "invalid_input"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"
    POLICY_BLOCKED = "policy_blocked"
    UNKNOWN_TOOL = "unknown_tool"


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class EvidenceItem(BaseModel):
    evidence_id: str
    source: str
    appid: str | None = None
    name: str = ""
    supported_fields: list[str] = Field(default_factory=list)
    score: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    status: ToolStatus
    data: Any = None
    error: ToolError | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


def normalize_tool_result(tool_name: str, value: Any, duration_seconds: float = 0.0) -> ToolResult:
    if isinstance(value, ToolResult):
        result = value
    elif isinstance(value, dict) and value.get("error"):
        result = ToolResult(
            status=ToolStatus.UPSTREAM_ERROR,
            data=value,
            error=ToolError(code="upstream_error", message=str(value["error"]), retryable=False),
        )
    else:
        is_empty = _is_empty(value)
        result = ToolResult(
            status=ToolStatus.EMPTY if is_empty else ToolStatus.SUCCESS,
            data=value,
            evidence=_extract_evidence(tool_name, value),
        )
    result.meta = {**result.meta, "duration_seconds": round(duration_seconds, 6), "tool": tool_name}
    return result


def policy_blocked_result(code: str, message: str) -> ToolResult:
    return ToolResult(
        status=ToolStatus.POLICY_BLOCKED,
        error=ToolError(code=code, message=message, retryable=False),
    )


def exception_result(exc: Exception) -> ToolResult:
    name = type(exc).__name__
    is_timeout = isinstance(exc, (TimeoutError, socket.timeout))
    is_rate_limit = isinstance(exc, urllib.error.HTTPError) and exc.code == 429
    is_invalid = isinstance(exc, (TypeError, ValueError))
    status = (
        ToolStatus.TIMEOUT if is_timeout
        else ToolStatus.RATE_LIMITED if is_rate_limit
        else ToolStatus.INVALID_INPUT if is_invalid
        else ToolStatus.UPSTREAM_ERROR
    )
    return ToolResult(
        status=status,
        error=ToolError(
            code=(
                "timeout" if is_timeout
                else "rate_limited" if is_rate_limit
                else "invalid_input" if is_invalid
                else "tool_exception"
            ),
            message=f"{name}: {exc}",
            retryable=is_timeout or is_rate_limit or isinstance(exc, (ConnectionError, urllib.error.URLError)),
        ),
    )


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        for key in ("results", "games", "memories", "messages"):
            if key in value:
                return not value[key]
    return value in ([], "")


def _extract_evidence(tool_name: str, value: Any) -> list[EvidenceItem]:
    if not isinstance(value, dict):
        return []
    rows = value.get("results") or value.get("games") or []
    evidence: list[EvidenceItem] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        appid = row.get("appid")
        if appid is None:
            continue
        supported = [
            key for key in (
                "name", "price", "metacritic", "tags", "description",
                "short_description", "playtime_forever", "playtime_2weeks",
                "is_free", "release_year", "has_multiplayer",
            ) if key in row
        ]
        evidence.append(EvidenceItem(
            evidence_id=f"{tool_name}:{appid}:{index}",
            source=tool_name,
            appid=str(appid),
            name=str(row.get("name", "")),
            supported_fields=supported,
            score=row.get("similarity_score"),
            payload={key: row[key] for key in supported},
        ))
    return evidence
