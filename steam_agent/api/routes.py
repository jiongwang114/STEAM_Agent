import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from ..graph.state import AgentState
from ..memory.archiver import archive_conversation
from ..tracing import set_trace_context
from .schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_graph():
    from ..graph.builder import build_graph

    return await build_graph()


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
    tool_calls_made = []

    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls_made.append(tc["name"])
        elif hasattr(msg, "content") and msg.content:
            reply = msg.content

    archive_conversation(
        user_id=req.user_id,
        thread_id=req.thread_id,
        user_message=req.message,
        assistant_reply=reply,
    )

    return ChatResponse(
        thread_id=req.thread_id,
        reply=reply,
        tool_calls_made=tool_calls_made,
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

        archive_conversation(
            user_id=req.user_id,
            thread_id=req.thread_id,
            user_message=req.message,
            assistant_reply=accumulated_reply,
        )

    return StreamingResponse(event_generator(), media_type="text/event-stream")
