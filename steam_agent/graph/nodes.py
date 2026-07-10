import json

from langchain_core.messages import AIMessage, ToolMessage

from ..config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, LLM_MAX_TOKENS, LLM_MODEL, LLM_TEMPERATURE
from ..prompts.system import build_system_prompt
from .state import AgentState


def guard_node(state: AgentState) -> dict:
    """Three-layer guard before the main agent.

    Layer 1: regex/rule-based (zero cost)
    Layer 2: LLM jailbreak intent classifier
    Layer 3: LLM scope boundary classifier

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

    # ── Layer 1: Regex rules (zero cost, zero latency) ──
    from ..guard.layer1_rules import check as layer1_check

    blocked, reason = layer1_check(text)
    if blocked:
        return {"messages": [AIMessage(content=f"GUARD_BLOCK:{reason}")]}

    # ── Layer 2: Jailbreak intent (LLM, ~0.5s) ──
    from ..guard.layer2_intent import check as layer2_check

    blocked, reason = layer2_check(text)
    if blocked:
        return {"messages": [AIMessage(content=f"GUARD_BLOCK:{reason}")]}

    # ── Layer 3: Scope boundary (LLM, ~0.5s) ──
    from ..guard.layer3_scope import check as layer3_check

    blocked, reason = layer3_check(text)
    if blocked:
        return {"messages": [AIMessage(content=f"GUARD_BLOCK:{reason}")]}

    # All clear
    return {"messages": []}


def build_llm():
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=LLM_MODEL,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )


def agent_node(state: AgentState) -> dict:
    llm = build_llm()
    tools = get_all_tools()
    llm_with_tools = llm.bind_tools(tools)

    user_id = state.get("user_id", "")
    steam_id = state.get("steam_id", "")

    system_prompt = build_system_prompt(user_id=user_id, steam_id=steam_id)

    messages = state["messages"]
    if not messages:
        return {"messages": []}

    full_messages = [system_prompt, *messages]

    response = llm_with_tools.invoke(full_messages)
    return {"messages": [response]}


def tool_node(state: AgentState) -> dict:
    messages = state["messages"]
    last_message = messages[-1]

    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        return {"messages": []}

    tool_map = get_tool_map()
    tool_messages: list[ToolMessage] = []

    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = dict(tool_call["args"])
        tool_call_id = tool_call["id"]

        # Auto-inject steam_id from state — LLM never sees the real value
        if tool_name == "get_user_playtime" and not tool_args.get("steam_id"):
            sid = state.get("steam_id", "")
            if sid:
                tool_args["steam_id"] = sid

        if tool_name in tool_map:
            try:
                result = tool_map[tool_name](**tool_args)
                content = json.dumps(result, ensure_ascii=False, indent=2)
            except Exception as exc:
                content = json.dumps({"error": str(exc)}, ensure_ascii=False)
        else:
            content = json.dumps({"error": f"Unknown tool: {tool_name}"})

        tool_messages.append(
            ToolMessage(content=content, tool_call_id=tool_call_id, name=tool_name)
        )

    return {"messages": tool_messages}


def get_all_tools():
    from ..tools.playtime import get_user_playtime
    from ..tools.rag_search import rag_search_similar_games
    from ..tools.store_search import search_steam_store
    from ..tools.user_insight import save_user_insight
    from ..tools.user_memory import recall_user_memory
    from ..tools.recall_message_detail import recall_message_detail

    return [
        get_user_playtime,
        search_steam_store,
        rag_search_similar_games,
        save_user_insight,
        recall_user_memory,
        recall_message_detail,
    ]


def get_tool_map() -> dict:
    from ..tools.playtime import get_user_playtime
    from ..tools.rag_search import rag_search_similar_games
    from ..tools.store_search import search_steam_store
    from ..tools.user_insight import save_user_insight
    from ..tools.user_memory import recall_user_memory
    from ..tools.recall_message_detail import recall_message_detail

    return {
        "get_user_playtime": get_user_playtime,
        "search_steam_store": search_steam_store,
        "rag_search_similar_games": rag_search_similar_games,
        "save_user_insight": save_user_insight,
        "recall_user_memory": recall_user_memory,
        "recall_message_detail": recall_message_detail,
    }


def should_continue(state: AgentState) -> str:
    messages = state["messages"]
    last_message = messages[-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return "__end__"
