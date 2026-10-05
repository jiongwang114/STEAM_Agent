"""Deterministic policy for controlling Agent tool execution.

Prompt instructions express desired behavior; this module enforces the costly
or safety-critical limits in code so model variance cannot cause runaway loops.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from config import AGENT_MAX_TOOL_CALLS_PER_ROUND, AGENT_MAX_TOOL_ROUNDS


TOOL_CALL_LIMITS: dict[str, int] = {
    "get_user_playtime": 1,
    "rag_search_similar_games": 2,
    "search_steam_store": 2,
    "recall_message_detail": 1,
    # Multiple distinct preferences may legitimately be saved in one turn.
    "save_user_insight": 5,
}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str = "allowed"
    message: str = ""


def canonical_tool_call(name: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return f"{name}:{payload}"


def collect_tool_calls(messages: Sequence[Any]) -> list[dict[str, Any]]:
    """Collect calls made after the most recent human message."""
    start = 0
    for index in range(len(messages) - 1, -1, -1):
        if getattr(messages[index], "type", "") == "human":
            start = index + 1
            break

    calls: list[dict[str, Any]] = []
    for message in messages[start:]:
        for call in getattr(message, "tool_calls", None) or []:
            calls.append({"name": call["name"], "args": dict(call.get("args", {}))})
    return calls


def count_tool_rounds(messages: Sequence[Any]) -> int:
    """Count assistant messages containing tool calls in the current turn."""
    return sum(
        1
        for message in _current_turn_messages(messages)
        if getattr(message, "tool_calls", None)
    )


def tool_budget_exhausted(messages: Sequence[Any]) -> bool:
    return count_tool_rounds(messages) >= AGENT_MAX_TOOL_ROUNDS


def evaluate_tool_call(
    name: str,
    arguments: dict[str, Any],
    prior_calls: Sequence[dict[str, Any]],
    position_in_round: int,
) -> PolicyDecision:
    if name == "get_user_playtime" and not str(arguments.get("steam_id") or "").strip():
        return PolicyDecision(
            False,
            "steam_binding_required",
            "未检测到已绑定的 Steam ID，不能读取游戏库或游玩记录。",
        )
    if position_in_round >= AGENT_MAX_TOOL_CALLS_PER_ROUND:
        return PolicyDecision(
            False,
            "round_call_limit",
            f"本轮最多执行 {AGENT_MAX_TOOL_CALLS_PER_ROUND} 个工具，请先使用已有结果。",
        )

    signature = canonical_tool_call(name, arguments)
    prior_signatures = {
        canonical_tool_call(call["name"], call.get("args", {})) for call in prior_calls
    }
    if signature in prior_signatures:
        return PolicyDecision(
            False,
            "duplicate_call",
            "相同参数的工具调用已经执行过，请直接使用已有结果。",
        )

    limit = TOOL_CALL_LIMITS.get(name)
    calls_for_tool = sum(1 for call in prior_calls if call["name"] == name)
    if limit is not None and calls_for_tool >= limit:
        return PolicyDecision(
            False,
            "tool_call_limit",
            f"{name} 本轮最多调用 {limit} 次，请使用已有结果或直接回答。",
        )

    return PolicyDecision(True)


def _current_turn_messages(messages: Sequence[Any]) -> Sequence[Any]:
    for index in range(len(messages) - 1, -1, -1):
        if getattr(messages[index], "type", "") == "human":
            return messages[index + 1:]
    return messages
