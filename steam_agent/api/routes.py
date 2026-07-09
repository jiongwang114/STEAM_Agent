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
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    accumulated_reply += chunk.content
                    yield f'data: {json.dumps({"event": "token", "data": chunk.content}, ensure_ascii=False)}\n\n'

            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                if tool_name:
                    tool_calls_seen.append(tool_name)
                    yield f'data: {json.dumps({"event": "tool_call", "data": tool_name}, ensure_ascii=False)}\n\n'

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

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── 前端历史记录 API ──

@router.get("/threads")
async def list_threads(user_id: str = Query(...)):
    threads = get_thread_list(user_id)
    return {"threads": threads}


@router.get("/messages")
async def read_messages(user_id: str = Query(...), thread_id: str = Query(...)):
    msgs = get_thread_messages(user_id, thread_id)
    return {"messages": msgs}


# ── Steam ID 绑定 API ──

@router.post("/bind-steam")
async def bind_steam(user_id: str = Query(...), steam_id: str = Query(...)):
    """将 Steam ID 持久化到 user_insights 表，作为个人事实保存。"""
    from ..memory.insight_store import add_insight, remove_insight, get_insights
    from ..memory.game_profile import get_game_profile

    # 移除旧的 steam_id 记录
    existing = get_insights(user_id)
    for item in existing:
        if "Steam ID" in item["insight"]:
            remove_insight(user_id, item["insight"])

    # 写入新的
    insight_text = f"用户Steam ID: {steam_id}"
    add_insight(user_id, insight_text, "fact")

    # 预热画像缓存
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
