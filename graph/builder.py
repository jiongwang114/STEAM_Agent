import asyncio

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langchain_core.messages import AIMessage

from config import CHECKPOINT_DB_PATH
from graph.nodes import (
    after_tools,
    after_validation,
    agent_node,
    finalize_node,
    guard_node,
    initialize_context_node,
    repair_node,
    safe_fallback_node,
    should_continue,
    tool_node,
    validate_answer_node,
)
from graph.state import AgentState

_graph = None
_conn: aiosqlite.Connection | None = None
_lock = asyncio.Lock()


async def build_graph(checkpointer=None):
    global _graph, _conn

    if checkpointer is not None:
        return _compile(checkpointer)

    if _graph is None:
        async with _lock:
            if _graph is None:
                _conn = await aiosqlite.connect(CHECKPOINT_DB_PATH)
                cp = AsyncSqliteSaver(_conn)
                _graph = _compile(cp)

    return _graph


async def close_graph() -> None:
    """Close the process-wide checkpoint connection during application shutdown."""
    global _graph, _conn
    async with _lock:
        connection = _conn
        _conn = None
        _graph = None
        if connection is not None:
            await connection.close()


def _compile(checkpointer):
    workflow = StateGraph(AgentState)

    workflow.add_node("guard", guard_node)
    workflow.add_node("initialize_context", initialize_context_node)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.add_node("finalize", finalize_node)
    workflow.add_node("validate", validate_answer_node)
    workflow.add_node("repair", repair_node)
    workflow.add_node("safe_fallback", safe_fallback_node)

    workflow.set_entry_point("initialize_context")
    workflow.add_edge("initialize_context", "guard")

    workflow.add_conditional_edges(
        "guard",
        _guard_decision,
        {"pass": "agent", "block": "guard_block"},
    )
    workflow.add_node("guard_block", guard_block_node)
    workflow.add_edge("guard_block", END)
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "validate": "validate"},
    )
    workflow.add_conditional_edges(
        "tools",
        after_tools,
        {"agent": "agent", "finalize": "finalize"},
    )
    workflow.add_edge("finalize", "validate")
    workflow.add_conditional_edges(
        "validate",
        after_validation,
        {"__end__": END, "repair": "repair", "safe_fallback": "safe_fallback"},
    )
    workflow.add_edge("repair", "validate")
    workflow.add_edge("safe_fallback", END)

    return workflow.compile(checkpointer=checkpointer)


def _guard_decision(state: AgentState) -> str:
    """Route based on guard_node's decision stored in messages."""
    messages = state["messages"]
    if not messages:
        return "block"
    last_msg = messages[-1]
    content = getattr(last_msg, "content", "") if hasattr(last_msg, "content") else str(last_msg)
    if state.get("guard_blocked") or "GUARD_BLOCK:" in content:
        return "block"
    return "pass"


def guard_block_node(state: AgentState) -> dict:
    return {
        "messages": [AIMessage(content="这个请求我无法协助处理。请换一个安全、合法的游戏推荐问题。")],
        "guard_blocked": True,
        "termination_reason": "guard_blocked",
    }
