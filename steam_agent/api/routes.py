import asyncio
import json
import logging
import threading
import time
import weakref
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from ..graph.state import AgentState
from ..config import (
    AGENT_MAX_WALL_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
    SESSION_TTL_SECONDS,
)
from ..memory.archiver import archive_conversation
from ..memory.auth import (
    bind_steam_id,
    create_session,
    get_user_info,
    login,
    revoke_all_sessions,
    revoke_session,
    register,
    update_theme,
)
from ..model_routing import assign_experiment
from ..memory.message_store import get_thread_list, get_thread_messages
from ..observability import record_agent_run
from ..graph.run_context import new_run_context, run_metadata
from ..tracing import set_trace_context
from .security import direct_call_user, qualified_thread_id, require_user
from .schemas import (
    AuthRequest,
    ChatRequest,
    ChatResponse,
    SteamBindRequest,
    ThemeRequest,
    ThreadTitleRequest,
)

logger = logging.getLogger(__name__)

_DEADLINE_REPLY = "这次检索超过了时间预算，我先停在这里，避免拿不完整的结果硬凑答案。请缩小一个条件后再试。"
_MODEL_ERROR_REPLY = "模型服务暂时不可用，这次无法可靠完成回答。请稍后重试；我不会用不完整结果拼凑答案。"

router = APIRouter()

_thread_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_thread_locks_guard = threading.Lock()

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


def _get_thread_lock(thread_key: str) -> asyncio.Lock:
    with _thread_locks_guard:
        lock = _thread_locks.get(thread_key)
        if lock is None:
            lock = asyncio.Lock()
            _thread_locks[thread_key] = lock
        return lock


def _identity(current_user, requested_user: str) -> str:
    user_id = direct_call_user(current_user, requested_user)
    if not user_id:
        raise ValueError("authenticated user is required")
    return user_id


def _steam_id_for_user(user_id: str, current_user, requested_steam_id: str | None) -> str:
    if isinstance(current_user, str):
        return (get_user_info(user_id) or {}).get("bound_steam_id", "")
    return requested_steam_id or ""


def _archive_turn(
    *,
    user_id: str,
    thread_id: str,
    user_message: str,
    assistant_reply: str,
) -> dict:
    try:
        return archive_conversation(
            user_id=user_id,
            thread_id=thread_id,
            user_message=user_message,
            assistant_reply=assistant_reply,
        )
    except Exception as exc:
        logger.warning(
            "archive_failed",
            extra={"fields": {
                "user_id": user_id,
                "thread_id": thread_id,
                "error_type": type(exc).__name__,
            }},
        )
        return {"status": "failed", "turn_number": None, "layers": {"sqlite": "error"}}


def _serializable_archive(result: dict) -> dict:
    return result if isinstance(result, dict) else {}


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


def _extract_executed_tool_calls(messages) -> list[str]:
    """Return attempted calls minus calls rejected by the deterministic policy."""
    attempted: list[tuple[str, str]] = []
    blocked_ids: set[str] = set()
    for msg in messages:
        for call in getattr(msg, "tool_calls", None) or []:
            attempted.append((call["id"], call["name"]))
        if getattr(msg, "type", "") != "tool":
            continue
        try:
            payload = json.loads(msg.content)
            if isinstance(payload, dict) and payload.get("status") == "policy_blocked":
                blocked_ids.add(msg.tool_call_id)
        except (json.JSONDecodeError, TypeError):
            continue
    return [name for call_id, name in attempted if call_id not in blocked_ids]


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    current_user: str = Depends(require_user),
) -> ChatResponse:
    started = time.perf_counter()
    user_id = _identity(current_user, req.user_id)
    steam_id = _steam_id_for_user(user_id, current_user, req.steam_id)
    thread_key = qualified_thread_id(user_id, req.thread_id)
    set_trace_context(user_id=user_id, thread_id=req.thread_id)

    config = {
        "configurable": {"thread_id": thread_key},
        "metadata": {
            "user_id": user_id,
            "thread_id": req.thread_id,
            "qualified_thread_id": thread_key,
        },
    }
    initial_state: AgentState = {
        "messages": [HumanMessage(content=req.message)],
        "steam_id": steam_id,
        "user_id": user_id,
        **new_run_context(),
        "experiment": assign_experiment(user_id, req.thread_id),
    }

    run_status = "success"
    try:
        async with _get_thread_lock(thread_key):
            graph = await _get_graph()
            async with asyncio.timeout(AGENT_MAX_WALL_SECONDS):
                result = await graph.ainvoke(initial_state, config)
    except TimeoutError:
        run_status = "degraded"
        result = {
            **initial_state,
            "messages": [*initial_state["messages"], AIMessage(content=_DEADLINE_REPLY)],
            "termination_reason": "deadline",
        }
    except Exception as exc:
        run_status = "degraded"
        logger.warning("agent_model_error", extra={"error_type": type(exc).__name__})
        result = {
            **initial_state,
            "messages": [*initial_state["messages"], AIMessage(content=_MODEL_ERROR_REPLY)],
            "termination_reason": "model_error",
        }

    messages = result["messages"]
    reply = ""
    tool_rounds = 0
    total_input_tokens = 0
    total_output_tokens = 0

    for msg in messages:
        if hasattr(msg, "content") and msg.content and not getattr(msg, "tool_calls", None):
            reply = msg.content

        usage = _extract_token_usage(msg)
        if usage:
            total_input_tokens += usage.get("prompt_tokens", usage.get("input_tokens", 0))
            total_output_tokens += usage.get("completion_tokens", usage.get("output_tokens", 0))

    history = list(result.get("tool_history") or [])
    tool_calls_made = [
        item["tool"] for item in history if item.get("status") != "policy_blocked"
    ]
    tool_rounds = len({item.get("round") for item in history if item.get("round") is not None})
    state_usage = result.get("usage") or {
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
    }
    total_input_tokens = int(state_usage.get("input_tokens", 0))
    total_output_tokens = int(state_usage.get("output_tokens", 0))

    archive = _archive_turn(
        user_id=user_id,
        thread_id=req.thread_id,
        user_message=req.message,
        assistant_reply=reply,
    )
    turn = archive.get("turn_number")
    if turn == 1 and reply:
        _maybe_generate_title(user_id, req.thread_id, req.message)

    record_agent_run(
        mode="sync",
        status=run_status,
        duration_seconds=time.perf_counter() - started,
        tool_calls=tool_calls_made,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
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
        run_metadata={**run_metadata(result), "archive": _serializable_archive(archive)},
    )


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    current_user: str = Depends(require_user),
):
    stream_started = time.perf_counter()
    observation = {"recorded": False}
    user_id = _identity(current_user, req.user_id)
    steam_id = _steam_id_for_user(user_id, current_user, req.steam_id)
    thread_key = qualified_thread_id(user_id, req.thread_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        first_token_at: float | None = None
        total_input_tokens = 0
        total_output_tokens = 0
        set_trace_context(user_id=user_id, thread_id=req.thread_id)

        config = {
            "configurable": {"thread_id": thread_key},
            "metadata": {
                "user_id": user_id,
                "thread_id": req.thread_id,
                "qualified_thread_id": thread_key,
            },
        }
        initial_state: AgentState = {
            "messages": [HumanMessage(content=req.message)],
            "steam_id": steam_id,
            "user_id": user_id,
            **new_run_context(),
            "experiment": assign_experiment(user_id, req.thread_id),
        }

        accumulated_reply = ""
        tool_calls_seen: list[str] = []

        deadline_reached = False
        model_error = False
        client_cancelled = False
        thread_lock = _get_thread_lock(thread_key)
        await thread_lock.acquire()
        try:
            graph = await _get_graph()
            async with asyncio.timeout(AGENT_MAX_WALL_SECONDS):
                async for event in graph.astream_events(initial_state, config, version="v2"):
                    kind = event.get("event", "")

                    if kind == "on_chat_model_stream":
                        # Skip guard LLM calls — they use langgraph_node metadata
                        if event.get("metadata", {}).get("langgraph_node") == "guard":
                            continue
                        chunk = event.get("data", {}).get("chunk")
                        if chunk and hasattr(chunk, "content") and chunk.content:
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
                            accumulated_reply += chunk.content
                            yield f'data: {json.dumps({"event": "token", "data": chunk.content}, ensure_ascii=False)}\n\n'

                    elif kind == "on_tool_start":
                        tool_name = event.get("name", "")
                        if tool_name and tool_name != "save_user_insight":
                            tool_calls_seen.append(tool_name)
                            text = _TOOL_STATUS.get(tool_name, "马上就好...")
                            yield f'data: {json.dumps({"event": "status", "data": text}, ensure_ascii=False)}\n\n'

                    elif kind == "on_chat_model_end":
                        if event.get("metadata", {}).get("langgraph_node") == "guard":
                            continue
                        output = event.get("data", {}).get("output")
                        usage = _extract_token_usage(output) if output is not None else None
                        if usage:
                            total_input_tokens += usage.get(
                                "prompt_tokens", usage.get("input_tokens", 0)
                            )
                            total_output_tokens += usage.get(
                                "completion_tokens", usage.get("output_tokens", 0)
                            )
        except asyncio.CancelledError:
            client_cancelled = True
            logger.info("agent_stream_cancelled")
        except TimeoutError:
            deadline_reached = True
            accumulated_reply = _DEADLINE_REPLY
            yield f'data: {json.dumps({"event": "token", "data": _DEADLINE_REPLY}, ensure_ascii=False)}\n\n'
        except Exception as exc:
            model_error = True
            accumulated_reply = _MODEL_ERROR_REPLY
            logger.warning("agent_stream_model_error", extra={"error_type": type(exc).__name__})
            yield f'data: {json.dumps({"event": "token", "data": _MODEL_ERROR_REPLY}, ensure_ascii=False)}\n\n'
        finally:
            thread_lock.release()

        if client_cancelled:
            archive = (
                _archive_turn(
                    user_id=user_id,
                    thread_id=req.thread_id,
                    user_message=req.message,
                    assistant_reply=accumulated_reply,
                )
                if accumulated_reply.strip()
                else {"status": "cancelled", "turn_number": None, "layers": {}}
            )
            record_agent_run(
                mode="stream",
                status="cancelled",
                duration_seconds=time.perf_counter() - stream_started,
                tool_calls=tool_calls_seen,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                ttft_seconds=(first_token_at - stream_started) if first_token_at else None,
            )
            observation["recorded"] = True
            return

        archive = _archive_turn(
            user_id=user_id,
            thread_id=req.thread_id,
            user_message=req.message,
            assistant_reply=accumulated_reply,
        )
        turn = archive.get("turn_number")

        # Auto-generate title after first turn
        if turn == 1 and accumulated_reply:
            _maybe_generate_title(user_id, req.thread_id, req.message)

        try:
            snapshot = await graph.aget_state(config)
            final_state = dict(snapshot.values)
        except Exception:
            final_state = initial_state

        final_metadata = run_metadata(final_state)
        final_metadata["archive"] = _serializable_archive(archive)
        if deadline_reached:
            final_metadata["termination_reason"] = "deadline"
        elif model_error:
            final_metadata["termination_reason"] = "model_error"
        final_usage = final_metadata.get("usage", {})
        if int(final_usage.get("total_tokens", 0)) > 0:
            total_input_tokens = int(final_usage.get("input_tokens", total_input_tokens))
            total_output_tokens = int(final_usage.get("output_tokens", total_output_tokens))
        else:
            final_usage.update({
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
            })

        record_agent_run(
            mode="stream",
            status="degraded" if deadline_reached or model_error else "success",
            duration_seconds=time.perf_counter() - stream_started,
            tool_calls=tool_calls_seen,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            ttft_seconds=(first_token_at - stream_started) if first_token_at else None,
        )
        observation["recorded"] = True

        # "done" means persistence and metrics have also completed.
        yield f'data: {json.dumps({"event": "done", "data": final_metadata}, ensure_ascii=False)}\n\n'

    async def observed_events() -> AsyncGenerator[str, None]:
        generator = event_generator()
        try:
            async for event in generator:
                yield event
        except Exception:
            record_agent_run(
                mode="stream",
                status="error",
                duration_seconds=time.perf_counter() - stream_started,
            )
            observation["recorded"] = True
            raise
        finally:
            await generator.aclose()
            if not observation["recorded"]:
                record_agent_run(
                    mode="stream",
                    status="cancelled",
                    duration_seconds=time.perf_counter() - stream_started,
                )
                observation["recorded"] = True

    return StreamingResponse(observed_events(), media_type="text/event-stream")


# ── 前端历史记录 API ──

@router.get("/threads")
async def list_threads(
    user_id: str | None = Query(default=None, include_in_schema=False),
    current_user: str = Depends(require_user),
):
    from ..memory.thread_title import get_thread_list_with_titles

    user_id = _identity(current_user, user_id or "")
    threads = get_thread_list_with_titles(user_id)
    if threads:
        return {"threads": threads}
    # fallback to old message-only list
    raw = get_thread_list(user_id)
    return {"threads": raw}


@router.post("/thread-title")
async def set_title(
    payload: ThreadTitleRequest,
    current_user: str = Depends(require_user),
):
    from ..memory.thread_title import set_thread_title

    set_thread_title(current_user, payload.thread_id, payload.title)
    return {"status": "ok"}


@router.get("/messages")
async def read_messages(
    thread_id: str = Query(..., min_length=1, max_length=128),
    user_id: str | None = Query(default=None, include_in_schema=False),
    current_user: str = Depends(require_user),
):
    user_id = _identity(current_user, user_id or "")
    msgs = get_thread_messages(user_id, thread_id)
    return {"messages": msgs}


@router.delete("/threads")
async def delete_thread(
    thread_id: str = Query(..., min_length=1, max_length=128),
    user_id: str | None = Query(default=None, include_in_schema=False),
    current_user: str = Depends(require_user),
):
    """Delete only the current user's thread and report layer outcomes."""
    from ..memory.message_store import delete_thread_detailed

    user_id = _identity(current_user, user_id or "")
    result = delete_thread_detailed(user_id, thread_id)
    if result["status"] == "not_found":
        return {"status": "error", "message": "会话不存在或无权操作", "layers": result["layers"]}
    return {
        "status": "ok" if result["status"] == "deleted" else "partial",
        "message": "会话已删除" if result["status"] == "deleted" else "会话已删除，部分后台清理待补偿",
        "layers": result["layers"],
    }


# ── Steam ID 绑定 API ──

@router.post("/bind-steam")
async def bind_steam(
    payload: SteamBindRequest,
    current_user: str = Depends(require_user),
):
    """绑定 Steam ID：auth 表 + user_insights 表 + 预热画像。"""
    from ..memory.insight_store import add_insight, remove_insight, get_insights
    from ..memory.game_profile import get_game_profile

    # Bind in auth table (one steam_id per user, check uniqueness)
    user_id = current_user
    steam_id = payload.steam_id
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
async def get_steam_id(current_user: str = Depends(require_user)):
    """查询已绑定的 Steam ID。"""
    return {"steam_id": (get_user_info(current_user) or {}).get("bound_steam_id", "")}


# ── 用户认证 API ──

@router.post("/auth/register")
async def auth_register(payload: AuthRequest, response: Response):
    ok, msg = register(payload.username, payload.password)
    if ok:
        token = create_session(payload.username.strip())
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            secure=SESSION_COOKIE_SECURE,
            samesite=SESSION_COOKIE_SAMESITE,
            path="/",
        )
    return {
        "status": "ok" if ok else "error",
        "message": msg,
        "username": payload.username if ok else "",
    }


@router.post("/auth/login")
async def auth_login(payload: AuthRequest, response: Response, request: Request):
    client_host = request.client.host if request.client else ""
    ok, msg = login(
        payload.username,
        payload.password,
        rate_limit_key=client_host or None,
    )
    if not ok:
        return {"status": "error", "message": msg, "username": ""}
    token = create_session(payload.username.strip())
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite=SESSION_COOKIE_SAMESITE,
        path="/",
    )
    return {"status": "ok", "message": msg, "username": payload.username.strip()}


@router.post("/auth/logout")
async def auth_logout(request: Request, response: Response):
    revoke_session(request.cookies.get(SESSION_COOKIE_NAME))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}


@router.post("/auth/revoke-all")
async def auth_revoke_all(
    response: Response,
    current_user: str = Depends(require_user),
):
    revoke_all_sessions(current_user)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}


@router.get("/auth/user-info")
async def auth_user_info(current_user: str = Depends(require_user)):
    info = get_user_info(current_user)
    if info:
        return {"status": "ok", "user": info}
    return {"status": "error", "message": "用户不存在"}


@router.post("/auth/theme")
async def auth_set_theme(
    payload: ThemeRequest,
    current_user: str = Depends(require_user),
):
    ok = update_theme(current_user, payload.theme)
    return {"status": "ok" if ok else "error"}


def _maybe_generate_title(user_id: str, thread_id: str, message: str):
    """Auto-generate a short title for a new thread based on the first user message."""
    try:
        from ..memory.thread_title import auto_generate_title
        auto_generate_title(user_id, thread_id, message)
    except Exception:
        pass
