from __future__ import annotations

import time
import json
from typing import Any, Sequence

from config import (
    AGENT_MAX_TOTAL_TOKENS,
    AGENT_MAX_TOOL_CALLS_PER_ROUND,
    AGENT_MAX_TOOL_ROUNDS,
    AGENT_MAX_WALL_SECONDS,
)


def new_run_context() -> dict[str, Any]:
    now = time.time()
    return {
        "budget": {
            "max_total_tokens": AGENT_MAX_TOTAL_TOKENS,
            "max_tool_rounds": AGENT_MAX_TOOL_ROUNDS,
            "max_tool_calls_per_round": AGENT_MAX_TOOL_CALLS_PER_ROUND,
            "deadline_at": now + AGENT_MAX_WALL_SECONDS,
        },
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "llm_calls": 0,
            "tool_calls": 0,
        },
        "tool_history": [],
        "evidence": [],
        "termination_reason": "",
        "repair_attempts": 0,
        "no_progress_rounds": 0,
        "constraints": [],
        "validation": {
            "passed": True,
            "unsupported_appids": [],
            "unsupported_prices": [],
            "unsupported_scores": [],
            "unsupported_discounts": [],
            "violations": [],
            "protocol_leak": False,
        },
        "context_stats": {"dropped_turns": 0, "estimated_tokens": 0},
        "experiment": {"name": "", "variant": "control", "bucket": 0},
        "model_history": [],
    }


def extract_token_usage(message: Any) -> dict[str, int]:
    for attribute in ("usage_metadata", "response_metadata"):
        metadata = getattr(message, attribute, None)
        if not isinstance(metadata, dict):
            continue
        for key in ("token_usage", "usage"):
            nested = metadata.get(key)
            if isinstance(nested, dict):
                metadata = nested
                break
        input_tokens = int(metadata.get("input_tokens", metadata.get("prompt_tokens", 0)) or 0)
        output_tokens = int(metadata.get("output_tokens", metadata.get("completion_tokens", 0)) or 0)
        if input_tokens or output_tokens:
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def add_usage(
    current: dict[str, int] | None,
    message: Any,
    input_components: dict[str, int] | None = None,
) -> dict[str, int]:
    usage = dict(current or {})
    observed = extract_token_usage(message)
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        usage[key] = int(usage.get(key, 0)) + observed[key]
    usage["llm_calls"] = int(usage.get("llm_calls", 0)) + 1
    usage.setdefault("tool_calls", 0)
    if input_components:
        estimated_total = sum(max(0, int(value)) for value in input_components.values())
        observed_input = observed["input_tokens"]
        for name, value in input_components.items():
            key = f"input_{name}_tokens"
            attributed = (
                round(observed_input * max(0, int(value)) / estimated_total)
                if estimated_total and observed_input
                else 0
            )
            usage[key] = int(usage.get(key, 0)) + attributed
    return usage


def budget_reason(state: dict[str, Any], now: float | None = None) -> str:
    budget = state.get("budget") or {}
    usage = state.get("usage") or {}
    now = time.time() if now is None else now
    if budget.get("deadline_at") and now >= float(budget["deadline_at"]):
        return "deadline"
    if budget.get("max_total_tokens") and int(usage.get("total_tokens", 0)) >= int(budget["max_total_tokens"]):
        return "token_budget"
    return ""


def run_metadata(state: dict[str, Any]) -> dict[str, Any]:
    budget = state.get("budget") or {}
    return {
        "termination_reason": state.get("termination_reason") or "completed",
        "usage": dict(state.get("usage") or {}),
        "budget": {
            key: value for key, value in budget.items() if key != "deadline_at"
        },
        "tool_history": list(state.get("tool_history") or []),
        "evidence": list(state.get("evidence") or []),
        "validation": dict(state.get("validation") or {}),
        "repair_attempts": int(state.get("repair_attempts", 0)),
        "context_stats": dict(state.get("context_stats") or {}),
        "experiment": dict(state.get("experiment") or {}),
        "model_history": list(state.get("model_history") or []),
    }


def merge_unique_evidence(existing: Sequence[dict], additions: Sequence[dict]) -> list[dict]:
    merged = {item["evidence_id"]: dict(item) for item in existing}
    for item in additions:
        merged[item["evidence_id"]] = dict(item)
    return list(merged.values())


def compact_messages(messages: Sequence[Any], max_tokens: int) -> tuple[list[Any], dict[str, int]]:
    groups = message_groups(messages)

    selected: list[list[Any]] = []
    estimated = 0
    for group in reversed(groups):
        group_tokens = sum(_message_tokens(message) for message in group)
        if selected and estimated + group_tokens > max_tokens:
            break
        selected.append(group)
        estimated += group_tokens
    selected.reverse()
    flattened = [message for group in selected for message in group]
    return flattened, {
        "dropped_turns": max(0, len(groups) - len(selected)),
        "estimated_tokens": estimated,
    }


def message_groups(messages: Sequence[Any]) -> list[list[Any]]:
    """Group a user turn with every following tool call and result."""
    groups: list[list[Any]] = []
    current: list[Any] = []
    for message in messages:
        if getattr(message, "type", "") == "human" and current:
            groups.append(current)
            current = [message]
        else:
            current.append(message)
    if current:
        groups.append(current)
    return groups


def _message_tokens(message: Any) -> int:
    content = str(getattr(message, "content", "") or "")
    tool_calls = getattr(message, "tool_calls", None) or []
    payload = content + (json.dumps(tool_calls, ensure_ascii=False, default=str) if tool_calls else "")
    ascii_count = sum(ord(character) < 128 for character in payload)
    return max(1, round(ascii_count / 4 + (len(payload) - ascii_count) / 1.5))
