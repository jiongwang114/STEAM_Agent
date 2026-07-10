import json
import logging
from collections import defaultdict
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from ..graph.state import AgentState
from ..memory.archiver import archive_conversation
from ..memory.message_store import get_thread_list, get_thread_messages
from ..tracing import set_trace_context
from .schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_turn_counter: dict[str, int] = defaultdict(int)

# Friendly status text shown while tools are running
_TOOL_STATUS: dict[str, str] = {
    "get_user_playtime": "让我翻翻你的游戏库...",
    "rag_search_similar_games": "帮你找找对味的游戏...",
    "search_steam_store": "瞄一眼商店价格...",
    "recall_user_memory": "回忆一下咱之前聊的...",
    "recall_message_detail": "翻翻之前的对话记录...",
}


async def _get_graph():
    from ..graph.builder import build_graph

    return await build_graph()


def _extract_token_usage(msg) -> dict | None:
    for attr in ("usage_metadata", "response_metadata"):
        meta = getattr(msg, attr, None)
        if meta and isinstance(meta, dict):
            for key in ("token_usage", "usage"):
                usage = meta.get(key)
                if usage and isinstance(usage, dict):
                    return usage
            if "prompt_tokens" in meta or "input_tokens" in meta:
                return meta
    return None


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    set_trace_context(user_id=req.user_id, thread_id=req.thread_id)

    graph = await _get_graph()
    config = {
        "configurable": {"thread_id": req.thread_id},
        "metadata": {"user_id": req.user_id, "thread_id": req.thread_id},
    }
    initial_state: AgentState = {
        "messages": [HumanMessage(content=req.message)],
        "steam_id": req.steam_id,
        "user_id": req.user_id,
    }

    result = await graph.ainvoke(initial_state, config)

    messages = result["messages"]
    reply = ""
    tool_calls_made: list[str] = []
    tool_rounds = 0
    total_input_tokens = 0
    total_output_tokens = 0

    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            tool_rounds += 1
            for tc in msg.tool_calls:
                tool_calls_made.append(tc["name"])
        elif hasattr(msg, "content") and msg.content and not getattr(msg, "tool_calls", None):
            reply = msg.content

        usage = _extract_token_usage(msg)
        if usage:
            total_input_tokens += usage.get("prompt_tokens", usage.get("input_tokens", 0))
            total_output_tokens += usage.get("completion_tokens", usage.get("output_tokens", 0))

    _turn_counter[req.thread_id] += 1
    turn = _turn_counter[req.thread_id]

    archive_conversation(
        user_id=req.user_id,
        thread_id=req.thread_id,
        user_message=req.message,
        assistant_reply=reply,
        turn_number=turn,
    )

    if turn == 1 and reply:
        _maybe_generate_title(req.user_id, req.thread_id, req.message)

    return ChatResponse(
        thread_id=req.thread_id,
        reply=reply,
        tool_calls_made=tool_calls_made,
        tool_rounds=tool_rounds,
        token_usage={
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
        },
    )


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    async def event_generator() -> AsyncGenerator[str, None]:
        set_trace_context(user_id=req.user_id, thread_id=req.thread_id)

        graph = await _get_graph()
        config = {
            "configurable": {"thread_id": req.thread_id},
            "metadata": {"user_id": req.user_id, "thread_id": req.thread_id},
        }
        initial_state: AgentState = {
            "messages": [HumanMessage(content=req.message)],
            "steam_id": req.steam_id,
            "user_id": req.user_id,
        }

        accumulated_reply = ""
        tool_calls_seen: list[str] = []

        async for event in graph.astream_events(initial_state, config, version="v2"):
            kind = event.get("event", "")

            if kind == "on_chat_model_stream":
                # Skip guard LLM calls — they use langgraph_node metadata
                if event.get("metadata", {}).get("langgraph_node") == "guard":
                    continue
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    accumulated_reply += chunk.content
                    yield f'data: {json.dumps({"event": "token", "data": chunk.content}, ensure_ascii=False)}\n\n'

            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                if tool_name and tool_name != "save_user_insight":
                    tool_calls_seen.append(tool_name)
                    text = _TOOL_STATUS.get(tool_name, "马上就好...")
                    yield f'data: {json.dumps({"event": "status", "data": text}, ensure_ascii=False)}\n\n'

        yield f'data: {json.dumps({"event": "done", "data": ""})}\n\n'

        _turn_counter[req.thread_id] += 1
        turn = _turn_counter[req.thread_id]

        archive_conversation(
            user_id=req.user_id,
            thread_id=req.thread_id,
            user_message=req.message,
            assistant_reply=accumulated_reply,
            turn_number=turn,
        )

        # Auto-generate title after first turn
        if turn == 1 and accumulated_reply:
            _maybe_generate_title(req.user_id, req.thread_id, req.message)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── 前端历史记录 API ──

@router.get("/threads")
async def list_threads(user_id: str = Query(...)):
    from ..memory.thread_title import get_thread_list_with_titles

    threads = get_thread_list_with_titles(user_id)
    if threads:
        return {"threads": threads}
    # fallback to old message-only list
    raw = get_thread_list(user_id)
    return {"threads": raw}


@router.post("/thread-title")
async def set_title(user_id: str = Query(...), thread_id: str = Query(...), title: str = Query(...)):
    from ..memory.thread_title import set_thread_title

    set_thread_title(user_id, thread_id, title)
    return {"status": "ok"}


@router.get("/messages")
async def read_messages(user_id: str = Query(...), thread_id: str = Query(...)):
    msgs = get_thread_messages(user_id, thread_id)
    return {"messages": msgs}


@router.delete("/threads")
async def delete_thread(user_id: str = Query(...), thread_id: str = Query(...)):
    """Delete a conversation thread and all its data across all storage layers."""
    from ..memory.message_store import delete_thread as do_delete

    ok = do_delete(user_id, thread_id)
    if ok:
        return {"status": "ok", "message": "会话已删除"}
    return {"status": "error", "message": "会话不存在或无权操作"}


# ── Steam ID 绑定 API ──

@router.post("/bind-steam")
async def bind_steam(user_id: str = Query(...), steam_id: str = Query(...)):
    """绑定 Steam ID：auth 表 + user_insights 表 + 预热画像。"""
    from ..memory.auth import bind_steam_id, get_user_info
    from ..memory.insight_store import add_insight, remove_insight, get_insights
    from ..memory.game_profile import get_game_profile

    # Bind in auth table (one steam_id per user, check uniqueness)
    ok, msg = bind_steam_id(user_id, steam_id)
    if not ok:
        return {"status": "error", "message": msg}

    # Persist as insight
    existing = get_insights(user_id)
    for item in existing:
        if "Steam ID" in item["insight"]:
            remove_insight(user_id, item["insight"])
    add_insight(user_id, f"用户Steam ID: {steam_id}", "fact")

    # Warm profile cache
    profile = get_game_profile(steam_id)

    return {"status": "ok", "steam_id": steam_id, "profile_preview": profile[:200] if profile else ""}


@router.get("/steam-id")
async def get_steam_id(user_id: str = Query(...)):
    """查询已绑定的 Steam ID。"""
    from ..memory.insight_store import get_insights

    existing = get_insights(user_id)
    for item in existing:
        if "Steam ID" in item["insight"]:
            return {"steam_id": item["insight"].replace("用户Steam ID: ", "").strip()}
    return {"steam_id": ""}


# ── 用户认证 API ──

@router.post("/auth/register")
async def auth_register(username: str = Query(...), password: str = Query(...)):
    from ..memory.auth import register

    ok, msg = register(username, password)
    return {"status": "ok" if ok else "error", "message": msg, "username": username if ok else ""}


@router.post("/auth/login")
async def auth_login(username: str = Query(...), password: str = Query(...)):
    from ..memory.auth import login

    ok, msg = login(username, password)
    return {"status": "ok" if ok else "error", "message": msg, "username": username if ok else ""}


@router.get("/auth/user-info")
async def auth_user_info(username: str = Query(...)):
    from ..memory.auth import get_user_info

    info = get_user_info(username)
    if info:
        return {"status": "ok", "user": info}
    return {"status": "error", "message": "用户不存在"}


@router.post("/auth/theme")
async def auth_set_theme(username: str = Query(...), theme: str = Query(...)):
    from ..memory.auth import update_theme

    update_theme(username, theme)
    return {"status": "ok"}


def _maybe_generate_title(user_id: str, thread_id: str, message: str):
    """Auto-generate a short title for a new thread based on the first user message."""
    try:
        from ..memory.thread_title import auto_generate_title
        auto_generate_title(user_id, thread_id, message)
    except Exception:
        pass
