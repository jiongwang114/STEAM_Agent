import inspect
import logging
import re
import time

from langchain_core.messages import AIMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from ..config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    AGENT_CONTEXT_WINDOW_TOKENS,
    AGENT_FINALIZE_MAX_TOKENS,
    AGENT_HISTORY_TOKEN_BUDGET,
    AGENT_HISTORY_COMPRESSION_THRESHOLD,
    AGENT_SUMMARY_MAX_TOKENS,
    AGENT_TOOL_CONTEXT_RESERVE_TOKENS,
    AGENT_MAX_REPAIR_ATTEMPTS,
    LLM_MAX_TOKENS,
    LLM_MAX_RETRIES,
    LLM_REQUEST_TIMEOUT_SECONDS,
    LLM_TEMPERATURE,
)
from ..observability import record_tool_execution
from ..model_routing import select_model
from ..memory.insight_store import get_insights
from ..memory.session_summary import get_latest_session_summary, save_session_summary
from ..prompts.system import build_system_prompt
from ..tools.contracts import (
    ToolError,
    ToolResult,
    ToolStatus,
    policy_blocked_result,
)
from ..tools.executor import execute_tool, execute_tool_batch
from .run_context import (
    add_usage,
    budget_reason,
    merge_unique_evidence,
    message_groups,
)
from .state import AgentState
from .tool_policy import (
    collect_tool_calls,
    count_tool_rounds,
    evaluate_tool_call,
    tool_budget_exhausted,
)


logger = logging.getLogger(__name__)


def initialize_context_node(state: AgentState) -> dict:
    """Load per-thread snapshots once; subsequent requests use checkpoint state."""
    update: dict = {}
    user_id = state.get("user_id", "")
    thread_id = state.get("thread_id", "")

    if "memory_snapshot" not in state:
        rows = get_insights(user_id, limit=12) if user_id else []
        update["memory_snapshot"] = rows

    if "steam_profile_snapshot" not in state:
        profile = ""
        steam_id = state.get("steam_id_snapshot", state.get("steam_id")) or ""
        update["steam_id_snapshot"] = steam_id
        if steam_id:
            try:
                from ..memory.game_profile import get_game_profile
                profile = get_game_profile(steam_id) or ""
            except Exception:
                logger.warning("steam_profile_snapshot_failed", exc_info=True)
        update["steam_profile_snapshot"] = profile

    if "conversation_summary" not in state:
        saved = get_latest_session_summary(user_id, thread_id) if user_id and thread_id else None
        update["conversation_summary"] = saved["summary"] if saved else ""
        update["summary_version"] = int(saved["version"]) if saved else 0
        update["summary_covered_to_turn"] = int(saved["covered_to_turn"]) if saved else 0

    return update


_SUMMARY_PROMPT = SystemMessage(content=(
    "你是会话摘要器。把给定的旧摘要和完整历史轮次压缩成一份准确、简洁的中文摘要。"
    "保留用户明确的偏好、限制、事实、已确认的决定、工具结果及仍待解决的问题；"
    "不要臆测，不要写与当前会话无关的内容，只输出摘要正文。"
))


def _system_prompt_for_state(state: AgentState) -> SystemMessage:
    return build_system_prompt(
        user_id=state.get("user_id", ""),
        steam_id=state.get("steam_id_snapshot", state.get("steam_id", "")),
        thread_id=state.get("thread_id", ""),
        insights=state.get("memory_snapshot"),
        steam_profile=state.get("steam_profile_snapshot"),
    )


def _prepare_model_context(
    state: AgentState,
    max_tokens: int,
) -> tuple[list, list[RemoveMessage], dict]:
    """Prepare dynamic history and synchronously persist a summary before inference."""
    messages = list(state.get("messages") or [])
    groups = message_groups(messages)
    summary = str(state.get("conversation_summary") or "").strip()
    summary_tokens = _estimate_text_tokens(summary)
    all_tokens = summary_tokens + sum(
        _message_tokens(message) for group in groups for message in group
    )
    stats = {
        "dropped_turns": 0,
        "estimated_tokens": all_tokens,
        "compression_triggered": False,
        "summary_version": int(state.get("summary_version", 0) or 0),
    }
    update: dict = {}
    selected_groups = groups
    summary_response = None

    threshold = max_tokens * AGENT_HISTORY_COMPRESSION_THRESHOLD
    compression_attempted = all_tokens >= threshold and len(groups) > 1
    if compression_attempted:
        # Keep the newest complete turns and summarize only whole user turns.
        keep_budget = max(_message_tokens(message) for message in groups[-1])
        keep_budget = max(keep_budget, int(max_tokens * 0.65))
        selected_reversed: list[list] = []
        selected_tokens = summary_tokens
        for group in reversed(groups):
            group_tokens = sum(_message_tokens(message) for message in group)
            if selected_reversed and selected_tokens + group_tokens > keep_budget:
                break
            selected_reversed.append(group)
            selected_tokens += group_tokens
        selected_groups = list(reversed(selected_reversed))
        dropped_groups = groups[: len(groups) - len(selected_groups)]
        if not dropped_groups:
            stats["compression_skipped"] = "no_complete_turns_to_compact"
            update["context_stats"] = stats
            return _dynamic_messages(summary, groups), [], update
        dropped_messages = [message for group in dropped_groups for message in group]

        source = []
        if summary:
            source.append(SystemMessage(content="旧摘要：\n" + summary))
        source.extend(dropped_messages)
        try:
            summary_response = build_llm(
                max_tokens=AGENT_SUMMARY_MAX_TOKENS,
                role="summary",
                experiment=state.get("experiment"),
            ).invoke([_SUMMARY_PROMPT, *source])
            new_summary = str(getattr(summary_response, "content", "") or "").strip()
        except Exception as exc:
            logger.warning("conversation_summary_failed", extra={"error_type": type(exc).__name__})
            new_summary = ""

        if new_summary:
            version = int(state.get("summary_version", 0) or 0) + 1
            covered_from = int(state.get("summary_covered_to_turn", 0) or 0) + 1
            covered_to = covered_from + len(dropped_groups) - 1
            thread_id = state.get("thread_id", "")
            message_ids = [getattr(message, "id", None) for message in dropped_messages]
            persisted = False
            if all(message_ids) and thread_id:
                try:
                    save_session_summary(
                        state.get("user_id", ""),
                        thread_id,
                        version,
                        covered_from,
                        covered_to,
                        new_summary,
                    )
                    persisted = True
                except Exception as exc:
                    logger.warning("conversation_summary_persist_failed", extra={"error_type": type(exc).__name__})

            if persisted and all(message_ids):
                update.update({
                    "conversation_summary": new_summary,
                    "summary_version": version,
                    "summary_covered_to_turn": covered_to,
                    "summary_persisted": bool(thread_id),
                })
                removals = [RemoveMessage(id=message_id) for message_id in message_ids]
                update["context_stats"] = {
                    **stats,
                    "dropped_turns": len(dropped_groups),
                    "estimated_tokens": _estimate_text_tokens(new_summary) + sum(
                        _message_tokens(message)
                        for group in selected_groups
                        for message in group
                    ),
                    "compression_triggered": True,
                    "summary_version": version,
                }
                stats = update["context_stats"]
                update["usage"] = add_usage(state.get("usage"), summary_response)
                update["model_history"] = [
                    *list(state.get("model_history") or []),
                    {
                        "role": "summary",
                        "model": select_model("summary", state.get("experiment")).model,
                        "variant": select_model("summary", state.get("experiment")).variant,
                    },
                ]
                return _dynamic_messages(new_summary, selected_groups), removals, update

        logger.warning(
            "conversation_compression_deferred",
            extra={
                "reason": (
                    "summary_not_persisted_or_messages_unidentified"
                    if new_summary else "summary_unavailable"
                )
            },
        )
        selected_groups = _select_recent_groups(
            groups, max(1, max_tokens - summary_tokens)
        )
        stats.update({
            "dropped_turns": max(0, len(groups) - len(selected_groups)),
            "compression_deferred": True,
        })
        if summary_response is not None:
            update["usage"] = add_usage(state.get("usage"), summary_response)
            selection = select_model("summary", state.get("experiment"))
            update["model_history"] = [
                *list(state.get("model_history") or []),
                {"role": "summary", "model": selection.model, "variant": selection.variant},
            ]
    elif all_tokens >= threshold and len(groups) <= 1:
        selected_groups = _select_recent_groups(
            groups, max(1, max_tokens - summary_tokens)
        )
        stats.update({
            "dropped_turns": max(0, len(groups) - len(selected_groups)),
            "compression_deferred": True,
        })

    stats["estimated_tokens"] = summary_tokens + sum(
        _message_tokens(message) for group in selected_groups for message in group
    )
    update["context_stats"] = stats
    return _dynamic_messages(summary, selected_groups), [], update


def _dynamic_messages(summary: str, groups: list[list]) -> list:
    dynamic = []
    if summary:
        dynamic.append(SystemMessage(content="## 当前会话摘要\n" + summary))
    dynamic.extend(message for group in groups for message in group)
    return dynamic


def _select_recent_groups(groups: list[list], max_tokens: int) -> list[list]:
    selected: list[list] = []
    estimated = 0
    for group in reversed(groups):
        group_tokens = sum(_message_tokens(message) for message in group)
        if selected and estimated + group_tokens > max_tokens:
            break
        selected.append(group)
        estimated += group_tokens
    return list(reversed(selected))


def _message_tokens(message) -> int:
    content = str(getattr(message, "content", "") or "")
    tool_calls = getattr(message, "tool_calls", None) or []
    payload = content
    if tool_calls:
        import json
        payload += json.dumps(tool_calls, ensure_ascii=False, default=str)
    ascii_count = sum(ord(character) < 128 for character in payload)
    return max(1, round(ascii_count / 4 + (len(payload) - ascii_count) / 1.5))


def _history_budget(
    system_prompt: SystemMessage,
    tools: list | None,
    output_tokens: int,
) -> int:
    fixed_tokens = _estimate_text_tokens(str(system_prompt.content or ""))
    for tool in tools or []:
        fixed_tokens += _estimate_text_tokens(
            f"{getattr(tool, '__name__', type(tool).__name__)}"
            f"{inspect.signature(tool)}{getattr(tool, '__doc__', '') or ''}"
        )
    reserved_tools = AGENT_TOOL_CONTEXT_RESERVE_TOKENS if tools else 0
    available = (
        AGENT_CONTEXT_WINDOW_TOKENS
        - fixed_tokens
        - max(0, output_tokens)
        - reserved_tools
    )
    return max(512, min(AGENT_HISTORY_TOKEN_BUDGET, available))


def guard_node(state: AgentState) -> dict:
    """Three-layer guard before the main agent.

    Layer 1: regex/rule-based — zero-width chars + political forbidden words (zero cost)
    Layer 2: LLM jailbreak / role-hijack classifier (~0.5s)
    Layer 3: LLM red-line classifier — adult content + politics (~0.5s)

    Short messages (< 4 chars) skip the LLM layer.
    On any layer blocking: returns AIMessage with GUARD_BLOCK marker.
    On all pass: returns empty dict (transparent).
    """
    messages = state["messages"]
    if not messages:
        return {"messages": []}

    # Get the last user message text
    last_msg = messages[-1]
    if hasattr(last_msg, "content"):
        text = last_msg.content if last_msg.content else ""
    else:
        text = str(last_msg) if last_msg else ""

    if not text.strip():
        # Empty message — let agent handle gracefully
        return {"messages": []}

    # Short messages don't carry injection/scope risk — skip LLM guard layers
    if len(text.strip()) < 4:
        from ..guard.layer1_rules import check as layer1_check
        blocked, reason = layer1_check(text)
        if blocked:
            return {"messages": [AIMessage(content=f"GUARD_BLOCK:{reason}")]}
        return {"messages": []}

    # ── Layer 1: Regex rules (zero cost, zero latency) ──
    from ..guard.layer1_rules import check as layer1_check

    blocked, reason = layer1_check(text)
    if blocked:
        return {"messages": [AIMessage(content=f"GUARD_BLOCK:{reason}")]}

    # ── Layer 2: jailbreak and role-hijack intent ──
    from ..guard.layer2_intent import check as layer2_check

    blocked, reason = layer2_check(text)
    if blocked:
        return {"messages": [AIMessage(content=f"GUARD_BLOCK:{reason}")]}

    # ── Layer 3: red-line classifier ──
    from ..guard.layer3_scope import check as layer3_check

    blocked, reason = layer3_check(text)
    if blocked:
        return {"messages": [AIMessage(content=f"GUARD_BLOCK:{reason}")]}

    # All clear
    return {"messages": []}


def build_llm(
    max_tokens: int | None = None,
    *,
    role: str = "agent",
    experiment: dict | None = None,
):
    from langchain_openai import ChatOpenAI

    selection = select_model(role, experiment)
    return ChatOpenAI(
        model=selection.model,
        temperature=LLM_TEMPERATURE,
        max_tokens=max_tokens or LLM_MAX_TOKENS,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        timeout=LLM_REQUEST_TIMEOUT_SECONDS,
        max_retries=LLM_MAX_RETRIES,
    )


def agent_node(state: AgentState) -> dict:
    selection = select_model("agent", state.get("experiment"))
    llm = build_llm(role="agent", experiment=state.get("experiment"))
    tools = get_all_tools()
    llm_with_tools = llm.bind_tools(tools)

    user_id = state.get("user_id", "")
    steam_id = state.get("steam_id_snapshot", state.get("steam_id", ""))

    system_prompt = _system_prompt_for_state(state)
    history_budget = _history_budget(system_prompt, tools, LLM_MAX_TOKENS)
    messages, removals, context_update = _prepare_model_context(
        state, history_budget
    )
    if not messages:
        return {"messages": []}

    full_messages = [system_prompt, *messages]

    response = llm_with_tools.invoke(full_messages)
    usage_state = context_update.get("usage", state.get("usage"))
    model_history = list(context_update.get("model_history", state.get("model_history") or []))
    return {
        "messages": [*removals, response],
        **context_update,
        "usage": add_usage(
            usage_state,
            response,
            _estimate_input_components(full_messages, tools),
        ),
        "model_history": [
            *model_history,
            {"role": "agent", "model": selection.model, "variant": selection.variant},
        ],
    }


def tool_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    messages = state["messages"]
    last_message = messages[-1]

    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        return {"messages": []}

    if _parallelizable(last_message.tool_calls):
        return _parallel_tool_node(state, config)

    configured = (config or {}).get("configurable", {})
    tool_map = configured.get("tool_map") or get_tool_map()
    tool_messages: list[ToolMessage] = []
    tool_history = list(state.get("tool_history") or [])
    evidence = list(state.get("evidence") or [])
    usage = dict(state.get("usage") or {})
    prior_calls = [
        {"name": call["name"], "args": _inject_tool_context(call["name"], call["args"], state)}
        for call in collect_tool_calls(messages[:-1])
    ]
    current_round = count_tool_rounds(messages)

    for position, tool_call in enumerate(last_message.tool_calls):
        tool_name = tool_call["name"]
        tool_args = _inject_tool_context(tool_name, dict(tool_call["args"]), state)
        tool_call_id = tool_call["id"]

        exhausted = budget_reason(state)
        decision = evaluate_tool_call(tool_name, tool_args, prior_calls, position)
        empty_result_seen = any(
            item.get("tool") == tool_name and item.get("status") == ToolStatus.EMPTY.value
            for item in tool_history
        )
        if exhausted:
            result = policy_blocked_result(
                "run_budget_exhausted",
                f"Agent run budget exhausted: {exhausted}. Use existing evidence.",
            )
            decision_allowed = False
        elif empty_result_seen and tool_name != "rag_search_similar_games":
            result = policy_blocked_result(
                "empty_result_no_retry",
                f"{tool_name} already returned empty; ask for clarification instead of retrying.",
            )
            decision_allowed = False
        else:
            result = None
            decision_allowed = decision.allowed
        if not decision_allowed:
            result = result or policy_blocked_result(decision.code, decision.message)
            content = result.model_dump_json(exclude_none=True)
            record_tool_execution(tool=tool_name, status="blocked", duration_seconds=0)
            tool_messages.append(
                ToolMessage(content=content, tool_call_id=tool_call_id, name=tool_name)
            )
            prior_calls.append({"name": tool_name, "args": tool_args})
            tool_history.append(
                _execution_record(tool_name, tool_args, result, 0.0, tool_call_id, current_round)
            )
            continue

        if tool_name in tool_map:
            started = time.perf_counter()
            result = execute_tool(
                tool_name,
                tool_map[tool_name],
                tool_args,
                deadline_at=(state.get("budget") or {}).get("deadline_at"),
            )
            duration = time.perf_counter() - started
            content = result.model_dump_json(exclude_none=True)
            record_tool_execution(
                tool=tool_name,
                status=result.status.value,
                duration_seconds=duration,
            )
        else:
            duration = 0.0
            result = ToolResult(
                status=ToolStatus.UNKNOWN_TOOL,
                error=ToolError(code="unknown_tool", message=f"Unknown tool: {tool_name}"),
            )
            content = result.model_dump_json(exclude_none=True)
            record_tool_execution(tool=tool_name, status="unknown", duration_seconds=0)

        tool_messages.append(
            ToolMessage(content=content, tool_call_id=tool_call_id, name=tool_name)
        )
        prior_calls.append({"name": tool_name, "args": tool_args})
        tool_history.append(
            _execution_record(tool_name, tool_args, result, duration, tool_call_id, current_round)
        )
        evidence = merge_unique_evidence(
            evidence,
            [item.model_dump(mode="json") for item in result.evidence],
        )
        usage["tool_calls"] = int(usage.get("tool_calls", 0)) + 1

    return {
        "messages": tool_messages,
        "tool_history": tool_history,
        "evidence": evidence,
        "usage": usage,
    }


def _inject_tool_context(tool_name: str, tool_args: dict, state: AgentState) -> dict:
    """Replace model-supplied context fields with authoritative graph state."""
    args = dict(tool_args)
    if tool_name == "get_user_playtime":
        args["steam_id"] = state.get(
            "steam_id_snapshot", state.get("steam_id", "")
        ) or ""
    if tool_name in {
        "save_user_insight",
        "recall_message_detail",
    }:
        args["user_id"] = state.get("user_id", "")
    return args


def _parallelizable(tool_calls: list[dict]) -> bool:
    if len(tool_calls) < 2:
        return False
    return sum(call["name"] == "save_user_insight" for call in tool_calls) <= 1


def _parallel_tool_node(state: AgentState, config: RunnableConfig | None) -> dict:
    messages = state["messages"]
    calls = messages[-1].tool_calls
    configured = (config or {}).get("configurable", {})
    tool_map = configured.get("tool_map") or get_tool_map()
    history = list(state.get("tool_history") or [])
    evidence = list(state.get("evidence") or [])
    usage = dict(state.get("usage") or {})
    prior_calls = [
        {"name": call["name"], "args": _inject_tool_context(call["name"], call["args"], state)}
        for call in collect_tool_calls(messages[:-1])
    ]
    current_round = count_tool_rounds(messages)
    results: list[ToolResult | None] = [None] * len(calls)
    prepared: list[tuple[int, str, dict, str]] = []
    requests = []

    for position, call in enumerate(calls):
        name = call["name"]
        arguments = _inject_tool_context(name, dict(call["args"]), state)
        call_id = call["id"]
        decision = evaluate_tool_call(name, arguments, prior_calls, position)
        exhausted = budget_reason(state)
        empty_seen = any(
            item.get("tool") == name and item.get("status") == ToolStatus.EMPTY.value
            for item in history
        )
        if exhausted:
            results[position] = policy_blocked_result(
                "run_budget_exhausted",
                f"Agent run budget exhausted: {exhausted}. Use existing evidence.",
            )
        elif empty_seen and name != "rag_search_similar_games":
            results[position] = policy_blocked_result(
                "empty_result_no_retry",
                f"{name} already returned empty; ask for clarification instead of retrying.",
            )
        elif not decision.allowed:
            results[position] = policy_blocked_result(decision.code, decision.message)
        elif name not in tool_map:
            results[position] = ToolResult(
                status=ToolStatus.UNKNOWN_TOOL,
                error=ToolError(code="unknown_tool", message=f"Unknown tool: {name}"),
            )
        else:
            prepared.append((position, name, arguments, call_id))
            requests.append({
                "tool_name": name,
                "function": tool_map[name],
                "arguments": arguments,
                "deadline_at": (state.get("budget") or {}).get("deadline_at"),
            })
        prior_calls.append({"name": name, "args": arguments})

    executed = execute_tool_batch(requests) if requests else []
    for prepared_item, result in zip(prepared, executed):
        results[prepared_item[0]] = result

    tool_messages = []
    prepared_positions = {item[0] for item in prepared}
    for position, call in enumerate(calls):
        result = results[position]
        assert result is not None
        name = call["name"]
        arguments = _inject_tool_context(name, dict(call["args"]), state)
        duration = float(result.meta.get("duration_seconds", 0))
        status = "blocked" if result.status == ToolStatus.POLICY_BLOCKED else result.status.value
        record_tool_execution(tool=name, status=status, duration_seconds=duration)
        tool_messages.append(ToolMessage(
            content=result.model_dump_json(exclude_none=True),
            tool_call_id=call["id"],
            name=name,
        ))
        history.append(_execution_record(
            name,
            arguments,
            result,
            duration,
            call["id"],
            current_round,
            parallel=position in prepared_positions,
        ))
        evidence = merge_unique_evidence(
            evidence,
            [item.model_dump(mode="json") for item in result.evidence],
        )
        if result.status != ToolStatus.POLICY_BLOCKED:
            usage["tool_calls"] = int(usage.get("tool_calls", 0)) + 1
    return {
        "messages": tool_messages,
        "tool_history": history,
        "evidence": evidence,
        "usage": usage,
    }


def _execution_record(
    tool_name: str,
    tool_args: dict,
    result: ToolResult,
    duration: float,
    tool_call_id: str,
    tool_round: int,
    parallel: bool = False,
) -> dict:
    return {
        "tool_call_id": tool_call_id,
        "round": tool_round,
        "tool": tool_name,
        "arguments": tool_args,
        "status": result.status.value,
        "error_code": result.error.code if result.error else "",
        "duration_seconds": round(duration, 6),
        "evidence_ids": [item.evidence_id for item in result.evidence],
        "parallel": parallel,
    }


def finalize_node(state: AgentState) -> dict:
    """Produce a final answer from gathered evidence with tools disabled."""
    budget = state.get("budget") or {}
    usage = state.get("usage") or {}
    max_tokens = int(budget.get("max_total_tokens", 0) or 0)
    used_tokens = int(usage.get("total_tokens", 0) or 0)
    if max_tokens and used_tokens + AGENT_FINALIZE_MAX_TOKENS > max_tokens:
        return {
            "messages": [AIMessage(content=_BUDGET_FALLBACK)],
            "termination_reason": "token_budget",
        }
    user_id = state.get("user_id", "")
    steam_id = state.get("steam_id_snapshot", state.get("steam_id", ""))
    base_prompt = _system_prompt_for_state(state)
    final_instruction = (
        "\n\n## 工具预算已用完\n"
        "你不能再调用工具。请严格依据当前消息中的工具结果直接给出最终答复。"
        "如果证据不足，明确说明缺少什么并向用户提出一个具体澄清问题；不要编造游戏、价格或评分。"
    )
    system_prompt = SystemMessage(content=base_prompt.content + final_instruction)
    history_budget = _history_budget(system_prompt, None, AGENT_FINALIZE_MAX_TOKENS)
    messages, removals, context_update = _prepare_model_context(
        state, history_budget
    )
    selection = select_model("finalize", state.get("experiment"))
    response = build_llm(
        max_tokens=AGENT_FINALIZE_MAX_TOKENS,
        role="finalize",
        experiment=state.get("experiment"),
    ).invoke(
        [system_prompt, *messages]
    )
    reason = budget_reason(state) or "tool_budget"
    usage_state = context_update.get("usage", state.get("usage"))
    model_history = list(context_update.get("model_history", state.get("model_history") or []))
    return {
        "messages": [*removals, response],
        **context_update,
        "usage": add_usage(
            usage_state,
            response,
            _estimate_input_components([system_prompt, *messages], []),
        ),
        "termination_reason": reason,
        "model_history": [
            *model_history,
            {"role": "finalize", "model": selection.model, "variant": selection.variant},
        ],
    }


_APP_URL_PATTERN = re.compile(r"store\.steampowered\.com/app/(\d+)", re.IGNORECASE)
_CNY_PRICE_PATTERN = re.compile(r"(?:CNY|RMB|[\u00a5\uffe5])\s*(\d+(?:\.\d{1,2})?)", re.IGNORECASE)
_METACRITIC_PATTERN = re.compile(r"(?:Metacritic|MC)\s*(?:评分)?\s*[:：]?\s*(\d{1,3})", re.IGNORECASE)
_DISCOUNT_PATTERN = re.compile(r"(?:折扣|优惠|off)\s*[:：]?\s*(\d{1,3})\s*%", re.IGNORECASE)
_PROTOCOL_LEAK_PATTERN = re.compile(
    r"(?:DSML|tool_calls|<\|\|.*?invoke|<invoke\b|tool_call_id)",
    re.IGNORECASE,
)


def validate_answer_node(state: AgentState) -> dict:
    answer = _latest_ai_content(state["messages"])
    recommended = list(dict.fromkeys(_APP_URL_PATTERN.findall(answer)))
    evidence = list(state.get("evidence", []))
    supported = {str(item.get("appid")) for item in evidence if item.get("appid") is not None}
    unsupported = [appid for appid in recommended if appid not in supported]
    unsupported_names = _unsupported_recommendation_names(answer, evidence)
    unsupported_prices = _unsupported_price_claims(answer, evidence)
    unsupported_scores = _unsupported_numeric_claims(
        answer, evidence, _METACRITIC_PATTERN, "metacritic"
    )
    unsupported_discounts = _unsupported_numeric_claims(
        answer, evidence, _DISCOUNT_PATTERN, "discount_percent"
    )
    violations = [f"unsupported_appid:{appid}" for appid in unsupported]
    violations.extend(
        f"unsupported_name:{item['appid']}:{item['claimed']}"
        for item in unsupported_names
    )
    violations.extend(
        f"unsupported_price:{item['appid']}:{item['claimed']}" for item in unsupported_prices
    )
    violations.extend(
        f"unsupported_metacritic:{item['appid']}:{item['claimed']}"
        for item in unsupported_scores
    )
    violations.extend(
        f"unsupported_discount:{item['appid']}:{item['claimed']}"
        for item in unsupported_discounts
    )
    protocol_leak = bool(_PROTOCOL_LEAK_PATTERN.search(answer))
    if protocol_leak:
        violations.append("internal_tool_protocol_leak")
    passed = not violations
    update = {
        "validation": {
            "passed": passed,
            "recommended_appids": recommended,
            "unsupported_appids": unsupported,
            "unsupported_names": unsupported_names,
            "unsupported_prices": unsupported_prices,
            "unsupported_scores": unsupported_scores,
            "unsupported_discounts": unsupported_discounts,
            "violations": violations,
            "protocol_leak": protocol_leak,
            "evidence_coverage": (
                (len(recommended) - len(unsupported)) / len(recommended)
                if recommended else 1.0
            ),
        }
    }
    if passed and not state.get("termination_reason"):
        update["termination_reason"] = "completed"
    return update


def repair_node(state: AgentState) -> dict:
    budget = state.get("budget") or {}
    usage = state.get("usage") or {}
    max_tokens = int(budget.get("max_total_tokens", 0) or 0)
    used_tokens = int(usage.get("total_tokens", 0) or 0)
    if max_tokens and used_tokens + AGENT_FINALIZE_MAX_TOKENS > max_tokens:
        return {
            "messages": [AIMessage(content=_BUDGET_FALLBACK)],
            "termination_reason": "token_budget",
        }
    unsupported = state.get("validation", {}).get("unsupported_appids", [])
    unsupported_names = state.get("validation", {}).get("unsupported_names", [])
    unsupported_prices = state.get("validation", {}).get("unsupported_prices", [])
    protocol_leak = state.get("validation", {}).get("protocol_leak", False)
    violations = state.get("validation", {}).get("violations", [])
    evidence_ids = [item.get("appid") for item in state.get("evidence", []) if item.get("appid")]
    base_prompt = _system_prompt_for_state(state)
    instruction = (
        "\n\n## Grounding repair\n"
        f"上一版回答引用了无证据 appid: {unsupported}。"
        f"上一版回答包含与证据不匹配的名称: {unsupported_names}。"
        f"上一版回答还包含无证据价格: {unsupported_prices}。"
        f"上一版回答是否泄露内部工具协议: {protocol_leak}。"
        f"需要修复的全部证据违规: {violations}。"
        f"本轮有证据的 appid 只有: {evidence_ids}。"
        "请重新输出完整最终答复，删除无证据推荐，不调用工具；如果证据不足，只提出一个具体澄清问题。"
    )
    repair_prompt = SystemMessage(content=base_prompt.content + instruction)
    history_budget = _history_budget(repair_prompt, None, AGENT_FINALIZE_MAX_TOKENS)
    messages, removals, context_update = _prepare_model_context(
        state, history_budget
    )
    selection = select_model("repair", state.get("experiment"))
    response = build_llm(
        max_tokens=AGENT_FINALIZE_MAX_TOKENS,
        role="repair",
        experiment=state.get("experiment"),
    ).invoke(
        [repair_prompt, *messages]
    )
    usage_state = context_update.get("usage", state.get("usage"))
    model_history = list(context_update.get("model_history", state.get("model_history") or []))
    return {
        "messages": [*removals, response],
        **context_update,
        "usage": add_usage(
            usage_state,
            response,
            _estimate_input_components(
                [repair_prompt, *messages],
                [],
            ),
        ),
        "repair_attempts": int(state.get("repair_attempts", 0)) + 1,
        "model_history": [
            *model_history,
            {"role": "repair", "model": selection.model, "variant": selection.variant},
        ],
    }


def safe_fallback_node(state: AgentState) -> dict:
    return {
        "messages": [AIMessage(content="现有检索结果不足以支持可靠推荐。你可以再告诉我一个喜欢的游戏、预算或想要的玩法，我再按这个条件帮你找。")],
        "termination_reason": "insufficient_evidence",
    }


_BUDGET_FALLBACK = (
    "这次对话已达到运行预算，我先停止检索，避免在不完整证据下编造推荐。"
    "请缩小一个条件后再试。"
)


def after_validation(state: AgentState) -> str:
    if state.get("validation", {}).get("passed", False):
        return "__end__"
    if int(state.get("repair_attempts", 0)) < AGENT_MAX_REPAIR_ATTEMPTS:
        return "repair"
    return "safe_fallback"


def _latest_ai_content(messages) -> str:
    for message in reversed(messages):
        if getattr(message, "type", "") == "ai" and not getattr(message, "tool_calls", None):
            return str(getattr(message, "content", ""))
    return ""


def _unsupported_recommendation_names(answer: str, evidence: list[dict]) -> list[dict]:
    evidence_by_appid: dict[str, list[str]] = {}
    for item in evidence:
        appid = item.get("appid")
        name = str(item.get("name") or "").strip()
        if appid is not None and name:
            evidence_by_appid.setdefault(str(appid), []).append(name)
    unsupported: list[dict] = []
    for match in re.finditer(
        r"\[([^\]]{1,120})\]\(https?://store\.steampowered\.com/app/(\d+)",
        answer,
        re.IGNORECASE,
    ):
        label = match.group(1).strip()
        names = evidence_by_appid.get(match.group(2), [])
        if names and not any(
            label.casefold() == name.casefold()
            or label.casefold() in name.casefold()
            or name.casefold() in label.casefold()
            for name in names
        ):
            unsupported.append({"appid": match.group(2), "claimed": label})
    return unsupported


def _estimate_input_components(messages, tools) -> dict[str, int]:
    components = {
        "system": 0,
        "history": 0,
        "tool_results": 0,
        "tool_schema": 0,
    }
    for message in messages:
        content = str(getattr(message, "content", "") or "")
        estimate = _estimate_text_tokens(content)
        message_type = getattr(message, "type", "")
        if message_type == "system":
            components["system"] += estimate
        elif message_type == "tool":
            components["tool_results"] += estimate
        else:
            components["history"] += estimate
    for tool in tools:
        signature = str(inspect.signature(tool))
        description = str(getattr(tool, "__doc__", "") or "")
        components["tool_schema"] += _estimate_text_tokens(
            f"{getattr(tool, '__name__', type(tool).__name__)}{signature}{description}"
        )
    return components


def _estimate_text_tokens(text: str) -> int:
    ascii_count = sum(ord(character) < 128 for character in text)
    non_ascii = len(text) - ascii_count
    return max(1, round(ascii_count / 4 + non_ascii / 1.5)) if text else 0


def _unsupported_price_claims(answer: str, evidence: list[dict]) -> list[dict]:
    prices_by_appid: dict[str, set[float]] = {}
    for item in evidence:
        appid = item.get("appid")
        price = (item.get("payload") or {}).get("price")
        if appid is None or not isinstance(price, dict):
            continue
        allowed = set()
        for key in ("initial", "final"):
            raw = price.get(key)
            if isinstance(raw, (int, float)):
                allowed.add(round(float(raw) / 100, 2))
        prices_by_appid[str(appid)] = allowed

    links = list(_APP_URL_PATTERN.finditer(answer))
    unsupported: list[dict] = []
    for index, match in enumerate(links):
        appid = match.group(1)
        end = links[index + 1].start() if index + 1 < len(links) else len(answer)
        section = answer[match.end():end]
        for price_match in _CNY_PRICE_PATTERN.finditer(section):
            claimed = round(float(price_match.group(1)), 2)
            if claimed not in prices_by_appid.get(appid, set()):
                item = {"appid": appid, "claimed": claimed}
                if item not in unsupported:
                    unsupported.append(item)
    return unsupported


def _unsupported_numeric_claims(
    answer: str,
    evidence: list[dict],
    pattern: re.Pattern,
    field: str,
) -> list[dict]:
    allowed_by_appid = {}
    for item in evidence:
        appid = item.get("appid")
        payload = item.get("payload") or {}
        if appid is None:
            continue
        if field == "discount_percent":
            value = (payload.get("price") or {}).get("discount_percent")
        else:
            value = payload.get(field)
        if isinstance(value, (int, float)):
            allowed_by_appid[str(appid)] = int(value)
    links = list(_APP_URL_PATTERN.finditer(answer))
    unsupported = []
    for index, match in enumerate(links):
        appid = match.group(1)
        end = links[index + 1].start() if index + 1 < len(links) else len(answer)
        for claim in pattern.findall(answer[match.end():end]):
            claimed = int(claim)
            if allowed_by_appid.get(appid) != claimed:
                item = {"appid": appid, "claimed": claimed}
                if item not in unsupported:
                    unsupported.append(item)
    return unsupported


def get_all_tools():
    from ..tools.playtime import get_user_playtime
    from ..tools.rag_search import rag_search_similar_games
    from ..tools.store_search import search_steam_store
    from ..tools.user_insight import save_user_insight
    from ..tools.recall_message_detail import recall_message_detail

    return [
        get_user_playtime,
        search_steam_store,
        rag_search_similar_games,
        save_user_insight,
        recall_message_detail,
    ]


def get_tool_map() -> dict:
    from ..tools.playtime import get_user_playtime
    from ..tools.rag_search import rag_search_similar_games
    from ..tools.store_search import search_steam_store
    from ..tools.user_insight import save_user_insight
    from ..tools.recall_message_detail import recall_message_detail

    return {
        "get_user_playtime": get_user_playtime,
        "search_steam_store": search_steam_store,
        "rag_search_similar_games": rag_search_similar_games,
        "save_user_insight": save_user_insight,
        "recall_message_detail": recall_message_detail,
    }


def should_continue(state: AgentState) -> str:
    messages = state["messages"]
    last_message = messages[-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return "validate"


def after_tools(state: AgentState) -> str:
    if tool_budget_exhausted(state["messages"]) or budget_reason(state):
        return "finalize"
    return "agent"
