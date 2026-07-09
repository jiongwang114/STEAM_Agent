import threading

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph

from ..config import CHECKPOINT_DB_PATH
from .nodes import agent_node, guard_node, should_continue, tool_node
from .state import AgentState

_graph = None
_conn: aiosqlite.Connection | None = None
_lock = threading.Lock()


async def build_graph(checkpointer=None):
    global _graph, _conn

    if checkpointer is not None:
        return _compile(checkpointer)

    if _graph is None:
        with _lock:
            if _graph is None:
                _conn = await aiosqlite.connect(CHECKPOINT_DB_PATH)
                cp = AsyncSqliteSaver(_conn)
                _graph = _compile(cp)

    return _graph


def _compile(checkpointer):
    workflow = StateGraph(AgentState)

    workflow.add_node("guard", guard_node)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)

    workflow.set_entry_point("guard")

    workflow.add_conditional_edges(
        "guard",
        _guard_decision,
        {"pass": "agent", "block": END},
    )
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "__end__": END},
    )
    workflow.add_edge("tools", "agent")

    return workflow.compile(checkpointer=checkpointer)


def _guard_decision(state: AgentState) -> str:
    """Route based on guard_node's decision stored in messages."""
    messages = state["messages"]
    if not messages:
        return "block"
    last_msg = messages[-1]
    content = getattr(last_msg, "content", "") if hasattr(last_msg, "content") else str(last_msg)
    if "GUARD_BLOCK:" in content:
        return "block"
    return "pass"
