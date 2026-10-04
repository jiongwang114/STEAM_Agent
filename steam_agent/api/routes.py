import asyncio
import json
import logging
import threading
import time
import weakref
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from ..graph.state import AgentState
from ..config import (
    AGENT_MAX_WALL_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
    SESSION_TTL_SECONDS,
    STEAM_PROFILE_WARMUP_TIMEOUT_SECONDS,
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
)
from ..model_routing import assign_experiment
from ..memory.message_store import get_thread_list, get_thread_messages
from ..observability import record_agent_run
from ..graph.run_context import new_run_context, run_metadata
from ..memory.session_summary import get_latest_session_summary
from ..tracing import set_trace_context
from .security import direct_call_user, qualified_thread_id, require_user
from .schemas import (
    AuthRequest,
    ChatRequest,
    ChatResponse,
    SteamBindRequest,
    ThreadTitleRequest,
)

logger = logging.getLogger(__name__)

_DEADLINE_REPLY = "这次检索超过了时间预算，我先停在这里，避免拿不完整的结果硬凑答案。请缩小一个条件后再试。"
_MODEL_ERROR_REPLY = "模型服务暂时不可用，这次无法可靠完成回答。请稍后重试；我不会用不完整结果拼凑答案。"

router = APIRouter()

_thread_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_thread_locks_guard = threading.Lock()


@dataclass
class _ChatRun:
    user_id: str
    thread_id: str
    request: ChatRequest
    steam_id: str
    started: float
    run_id: str
    task: asyncio.Task | None = None
    listeners: set[asyncio.Queue] = field(default_factory=set)
    initial_events: list[dict] = field(default_factory=list)
    initial_stream_attached: bool = False
    reply: str = ""
    stream_status: str = "正在理解问题"
    progress: list[dict] = field(default_factory=list)
    result: dict | None = None
    last_error: dict | None = None
    state: str = "running"
    terminal_event: dict | None = None
    finished_at: float | None = None

    def snapshot(self) -> dict:
        return {
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "message": self.request.message,
            "reply": self.reply,
            "status": self.state,
            "stream_status": self.stream_status,
            "progress": list(self.progress),
            "error": self.last_error,
            "result": self.result,
        }

    def publish(self, event: dict) -> None:
        event_name = event.get("event")
        data = event.get("data")
        if event_name == "token" and isinstance(data, str):
            self.reply += data
        elif event_name == "status" and isinstance(data, str):
            self.stream_status = data
            for step in self.progress:
                if step["status"] == "in_progress":
                    step["status"] = "completed"
            self.progress.append({"label": data, "status": "in_progress"})
        elif event_name == "error" and isinstance(data, dict):
            self.last_error = data
        elif event_name == "done" and isinstance(data, dict):
            self.state = "completed"
            self.result = data
            self.reply = str(data.get("reply") or self.reply)
            self.progress = [
                {**step, "status": "completed"} for step in self.progress
            ]
        elif event_name == "cancelled":
            self.state = "cancelled"
        if event_name in {"done", "cancelled"}:
            self.terminal_event = event
            self.finished_at = time.monotonic()
        if not self.initial_stream_attached:
            self.initial_events.append(event)
        for listener in tuple(self.listeners):
            listener.put_nowait(event)


_chat_runs: dict[tuple[str, str], _ChatRun] = {}
_chat_runs_guard = threading.Lock()
_CHAT_RUN_RETENTION_SECONDS = 600


def _find_chat_run(user_id: str, thread_id: str) -> _ChatRun | None:
    now = time.monotonic()
    key = (user_id, thread_id)
    with _chat_runs_guard:
        for stale_key, run in tuple(_chat_runs.items()):
            if run.finished_at is not None and now - run.finished_at > _CHAT_RUN_RETENTION_SECONDS:
                _chat_runs.pop(stale_key, None)
        return _chat_runs.get(key)


def _active_chat_threads(user_id: str) -> set[str]:
    now = time.monotonic()
    with _chat_runs_guard:
        for stale_key, run in tuple(_chat_runs.items()):
            if run.finished_at is not None and now - run.finished_at > _CHAT_RUN_RETENTION_SECONDS:
                _chat_runs.pop(stale_key, None)
        return {
            thread_id for owner, thread_id in _chat_runs
            if owner == user_id and _chat_runs[(owner, thread_id)].state == "running"
        }


def _create_chat_run(
    req: ChatRequest, *, user_id: str, steam_id: str, started: float
) -> _ChatRun:
    key = (user_id, req.thread_id)
    with _chat_runs_guard:
        previous = _chat_runs.get(key)
        if previous is not None and previous.state == "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "thread_run_active", "message": "该会话仍有回答正在生成"},
            )
        run = _ChatRun(
            user_id=user_id,
            thread_id=req.thread_id,
            request=req,
            steam_id=steam_id,
            started=started,
            run_id=f"run-{time.time_ns():x}",
        )
        _chat_runs[key] = run
    run.task = asyncio.create_task(_produce_chat_run(run), name=f"chat:{run.run_id}")
    return run


async def _produce_chat_run(run: _ChatRun) -> None:
    events = _execute_agent_events(
        run.request,
        user_id=run.user_id,
        steam_id=run.steam_id,
        mode="stream",
        started=run.started,
    )
    try:
        async for event in events:
            run.publish(event)
        if run.terminal_event is None:
            run.publish({"event": "cancelled", "data": {"reply": run.reply}})
    except asyncio.CancelledError:
        try:
            await events.aclose()
        finally:
            if run.terminal_event is None:
                run.publish({"event": "cancelled", "data": {"reply": run.reply}})
    except Exception as exc:
        logger.warning("chat_run_failed", extra={"error_type": type(exc).__name__})
        if run.terminal_event is None:
            run.publish({
                "event": "error",
                "data": {"code": "agent_unavailable", "message": _MODEL_ERROR_REPLY},
            })
            run.publish({"event": "cancelled", "data": {"reply": run.reply}})
    finally:
        run.finished_at = run.finished_at or time.monotonic()


async def _chat_run_stream(
    run: _ChatRun, *, include_snapshot: bool = True
) -> AsyncGenerator[str, None]:
    listener: asyncio.Queue = asyncio.Queue()
    snapshot = run.snapshot()
    initial_events = [] if include_snapshot else list(run.initial_events)
    run.initial_events.clear()
    run.initial_stream_attached = True
    run.listeners.add(listener)
    try:
        if include_snapshot:
            yield _sse({"event": "snapshot", "data": snapshot})
        else:
            for event in initial_events:
                yield _sse(event)
                if event.get("event") in {"done", "cancelled"}:
                    return
        if run.terminal_event is not None:
            yield _sse(run.terminal_event)
            return
        while True:
            try:
                event = await asyncio.wait_for(listener.get(), timeout=15)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield _sse(event)
            if event.get("event") in {"done", "cancelled"}:
                return
    finally:
        run.listeners.discard(listener)

# Friendly status text shown while tools are running
_TOOL_STATUS: dict[str, str] = {
    "get_user_playtime": "让我翻翻你的游戏库...",
    "rag_search_similar_games": "帮你找找对味的游戏...",
    "search_steam_store": "瞄一眼商店价格...",
    "recall_message_detail": "翻翻之前的对话记录...",
}

_TOOL_DISPLAY_NAMES: dict[str, str] = {
    "get_user_playtime": "读取你的游戏库",
    "rag_search_similar_games": "查找相似游戏",
    "search_steam_store": "查询 Steam 商店",
    "recall_message_detail": "查找历史对话",
}

_PUBLIC_STEP_STATUS = {
    "success": "completed",
    "empty": "empty",
    "policy_blocked": "blocked",
}


async def _get_graph():
    from ..graph.builder import build_graph

    return await build_graph()


async def _restore_archived_history(graph, config: dict, initial_state: AgentState) -> AgentState:
    """Rebuild raw text history only when the checkpoint is missing or empty."""
    try:
        snapshot = await graph.aget_state(config)
        if snapshot.values.get("messages"):
            return initial_state
    except Exception:
        pass

    user_id = initial_state.get("user_id", "")
    thread_id = initial_state.get("thread_id", "")
    if not user_id or not thread_id:
        return initial_state
    summary = get_latest_session_summary(user_id, thread_id)
    covered_to = int(summary.get("covered_to_turn", 0)) if summary else 0
    archived = get_thread_messages(user_id, thread_id)
    restored = []
    for item in archived:
        if int(item["turn"]) <= covered_to:
            continue
        if item["role"] == "user":
            restored.append(HumanMessage(content=item["content"]))
        elif item["role"] == "assistant":
            restored.append(AIMessage(content=item["content"]))
    if restored:
        initial_state["messages"] = [*restored, *initial_state["messages"]]
    if summary:
        initial_state.update({
            "conversation_summary": summary["summary"],
            "summary_version": int(summary["version"]),
            "summary_covered_to_turn": covered_to,
        })
    return initial_state


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
    execution: dict | None = None,
) -> dict:
    try:
        return archive_conversation(
            user_id=user_id,
            thread_id=thread_id,
            user_message=user_message,
            assistant_reply=assistant_reply,
            execution=execution,
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


def _execution_summary(
    status_value: str,
    tool_history: list[dict],
    duration_seconds: float,
    fallback_tools: list[str] | None = None,
) -> dict:
    """Convert internal tool history into a safe, replayable public summary."""
    steps: list[dict] = []
    for item in tool_history:
        tool_name = str(item.get("tool", ""))
        if not tool_name or tool_name == "save_user_insight":
            continue
        raw_status = str(item.get("status", "unknown"))
        steps.append({
            "name": _TOOL_DISPLAY_NAMES.get(tool_name, "执行检索步骤"),
            "status": _PUBLIC_STEP_STATUS.get(raw_status, "failed"),
            "duration_ms": max(0, round(float(item.get("duration_seconds", 0) or 0) * 1000)),
            "round": max(0, int(item.get("round", 0) or 0)),
        })
    if not steps:
        seen: set[str] = set()
        for tool_name in fallback_tools or []:
            if tool_name and tool_name != "save_user_insight" and tool_name not in seen:
                seen.add(tool_name)
                steps.append({
                    "name": _TOOL_DISPLAY_NAMES.get(tool_name, "执行检索步骤"),
                    "status": "in_progress" if status_value == "cancelled" else "failed",
                    "duration_ms": 0,
                    "round": 0,
                })
    return {
        "status": status_value,
        "duration_ms": max(0, round(duration_seconds * 1000)),
        "steps": steps,
    }


def _schedule_title_generation(user_id: str, thread_id: str, message: str) -> None:
    """Claim the first-turn title before the Agent starts, then run it off-loop."""
    from ..memory.thread_title import claim_auto_title_generation

    if not claim_auto_title_generation(user_id, thread_id):
        return

    async def generate() -> None:
        try:
            await asyncio.to_thread(_maybe_generate_title, user_id, thread_id, message)
        except Exception as exc:
            logger.warning(
                "auto_title_generation_failed",
                extra={"fields": {"error_type": type(exc).__name__}},
            )

    task = asyncio.create_task(generate())
    task.add_done_callback(
        lambda completed: completed.exception() if not completed.cancelled() else None
    )


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
    return [
        name for call_id, name in attempted
        if call_id not in blocked_ids and name != "save_user_insight"
    ]


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _normalize_reply(raw_reply: str) -> tuple[str, dict]:
    """Return the canonical summary/games envelope used by API and SSE clients."""
    raw = str(raw_reply or "").strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    parsed = None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        # Tool-call turns can contribute text before the final answer. Recover
        # the last complete envelope instead of exposing that protocol text.
        decoder = json.JSONDecoder()
        for index, char in enumerate(raw):
            if char != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(raw[index:])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(candidate, dict) and isinstance(candidate.get("summary"), str) and isinstance(candidate.get("games"), list):
                parsed = candidate

    if isinstance(parsed, dict) and isinstance(parsed.get("summary"), str) and isinstance(parsed.get("games"), list):
        games = []
        for item in parsed["games"]:
            if not isinstance(item, dict) or not item.get("appid") or not item.get("name") or not item.get("store_url"):
                continue
            games.append({
                "appid": str(item.get("appid", "")),
                "name": str(item.get("name", "")),
                "store_url": str(item.get("store_url", "")),
                "image_url": str(item.get("image_url", "")),
                "reason": str(item.get("reason", "")),
            })
        envelope = {"summary": parsed["summary"], "games": games}
    else:
        envelope = {"summary": raw, "games": []}
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")), envelope


async def _execute_agent_events(
    req: ChatRequest,
    *,
    user_id: str,
    steam_id: str,
    mode: str,
    started: float,
) -> AsyncGenerator[dict, None]:
    """Single LangGraph execution path shared by sync and SSE endpoints."""
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
        "thread_id": req.thread_id,
        **new_run_context(),
        "experiment": assign_experiment(user_id, req.thread_id),
    }
    final_state: dict = initial_state
    graph = None
    reply = ""
    first_token_at: float | None = None
    input_tokens = 0
    output_tokens = 0
    tool_calls_seen: list[str] = []
    termination_reason = ""
    metrics_recorded = False

    def record(status_value: str, tools: list[str] | None = None) -> None:
        nonlocal metrics_recorded
        if metrics_recorded:
            return
        record_agent_run(
            mode=mode,
            status=status_value,
            duration_seconds=time.perf_counter() - started,
            tool_calls=tools or [],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            ttft_seconds=(first_token_at - started) if first_token_at else None,
        )
        metrics_recorded = True

    try:
        async with _get_thread_lock(thread_key):
            graph = await _get_graph()
            initial_state = await _restore_archived_history(graph, config, initial_state)
            final_state = initial_state
            async with asyncio.timeout(AGENT_MAX_WALL_SECONDS):
                async for event in graph.astream_events(initial_state, config, version="v2"):
                    kind = event.get("event", "")
                    metadata = event.get("metadata", {})
                    if kind == "on_chat_model_stream":
                        if metadata.get("langgraph_node") == "guard":
                            continue
                        chunk = event.get("data", {}).get("chunk")
                        content = getattr(chunk, "content", None)
                        if isinstance(content, str) and content:
                            first_token_at = first_token_at or time.perf_counter()
                            reply += content
                            yield {"event": "token", "data": content}
                    elif kind == "on_tool_start":
                        tool_name = event.get("name", "")
                        if tool_name and tool_name != "save_user_insight":
                            tool_calls_seen.append(tool_name)
                            yield {
                                "event": "status",
                                "data": _TOOL_STATUS.get(tool_name, "马上就好..."),
                            }
                    elif kind == "on_chat_model_end":
                        if metadata.get("langgraph_node") == "guard":
                            continue
                        output = event.get("data", {}).get("output")
                        usage = _extract_token_usage(output) if output is not None else None
                        if usage:
                            input_tokens += int(usage.get("prompt_tokens", usage.get("input_tokens", 0)))
                            output_tokens += int(usage.get("completion_tokens", usage.get("output_tokens", 0)))
            try:
                snapshot = await graph.aget_state(config)
                final_state = dict(snapshot.values)
            except Exception:
                pass
    except TimeoutError:
        termination_reason = "deadline"
        reply = _DEADLINE_REPLY
        yield {"event": "error", "data": {"code": "agent_timeout", "message": _DEADLINE_REPLY}}
        yield {"event": "token", "data": _DEADLINE_REPLY}
    except asyncio.CancelledError:
        termination_reason = "cancelled"
        if reply.strip():
            execution = _execution_summary(
                "cancelled",
                list(final_state.get("tool_history") or []),
                time.perf_counter() - started,
                tool_calls_seen,
            )
            _archive_turn(
                user_id=user_id,
                thread_id=req.thread_id,
                user_message=req.message,
                assistant_reply=_normalize_reply(reply)[0],
                execution=execution,
            )
        record("cancelled", tool_calls_seen)
        return
    except GeneratorExit:
        if reply.strip():
            execution = _execution_summary(
                "cancelled",
                list(final_state.get("tool_history") or []),
                time.perf_counter() - started,
                tool_calls_seen,
            )
            _archive_turn(
                user_id=user_id,
                thread_id=req.thread_id,
                user_message=req.message,
                assistant_reply=_normalize_reply(reply)[0],
                execution=execution,
            )
        record("cancelled", tool_calls_seen)
        raise
    except Exception as exc:
        termination_reason = "model_error"
        logger.warning("agent_model_error", extra={"error_type": type(exc).__name__})
        reply = _MODEL_ERROR_REPLY
        yield {"event": "error", "data": {"code": "agent_unavailable", "message": _MODEL_ERROR_REPLY}}
        yield {"event": "token", "data": _MODEL_ERROR_REPLY}

    messages = final_state.get("messages") or []
    if not reply:
        for message in reversed(messages):
            content = getattr(message, "content", "")
            if isinstance(content, str) and content and not getattr(message, "tool_calls", None):
                reply = content
                break

    reply, _reply_payload = _normalize_reply(reply)

    history = list(final_state.get("tool_history") or [])
    tool_calls = [
        item["tool"] for item in history
        if item.get("status") != "policy_blocked" and item.get("tool") != "save_user_insight"
    ]
    if not tool_calls:
        tool_calls = _extract_executed_tool_calls(messages)
    tool_rounds = len({item.get("round") for item in history if item.get("round") is not None})
    usage = final_state.get("usage") or {}
    input_tokens = int(usage.get("input_tokens", input_tokens))
    output_tokens = int(usage.get("output_tokens", output_tokens))

    metadata = run_metadata(final_state)
    # Internal tool arguments, evidence payloads and model history are not API fields.
    metadata.pop("tool_history", None)
    metadata.pop("evidence", None)
    metadata.pop("model_history", None)
    state_reason = metadata.get("termination_reason", "completed")
    if not termination_reason and state_reason not in {"", "completed"}:
        termination_reason = str(state_reason)
    if termination_reason:
        metadata["termination_reason"] = termination_reason
    response_status = "degraded" if termination_reason else "success"
    execution = _execution_summary(
        response_status,
        history,
        time.perf_counter() - started,
        tool_calls_seen,
    )
    archive = _archive_turn(
        user_id=user_id,
        thread_id=req.thread_id,
        user_message=req.message,
        assistant_reply=reply,
        execution=execution,
    )
    metadata["archive"] = _serializable_archive(archive)
    if not termination_reason and archive.get("status") != "complete":
        termination_reason = "archive_failed"
        metadata["termination_reason"] = termination_reason
        response_status = "degraded"
        execution = _execution_summary(
            response_status,
            history,
            time.perf_counter() - started,
            tool_calls_seen,
        )
    metadata["execution"] = execution
    response = ChatResponse(
        status=response_status,
        thread_id=req.thread_id,
        reply=reply,
        tool_calls_made=tool_calls,
        tool_rounds=tool_rounds,
        token_usage={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        execution=execution,
        run_metadata=metadata,
    )
    record(response_status, tool_calls)
    yield {"event": "done", "data": response.model_dump(mode="json")}


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    current_user: str = Depends(require_user),
) -> ChatResponse:
    user_id = _identity(current_user, req.user_id or "")
    from ..memory.message_store import thread_cleanup_is_pending

    if thread_cleanup_is_pending(user_id, req.thread_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "thread_cleanup_pending", "message": "该会话正在清理，请稍后重试"},
        )
    _schedule_title_generation(user_id, req.thread_id, req.message)
    steam_id = _steam_id_for_user(user_id, current_user, req.steam_id)
    events = _execute_agent_events(
        req,
        user_id=user_id,
        steam_id=steam_id,
        mode="sync",
        started=time.perf_counter(),
    )
    async for event in events:
        if event.get("event") == "done":
            return ChatResponse.model_validate(event["data"])
    raise HTTPException(
        status_code=500,
        detail={"code": "agent_incomplete", "message": "Agent 未能完成本次请求"},
    )


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    current_user: str = Depends(require_user),
):
    user_id = _identity(current_user, req.user_id or "")
    from ..memory.message_store import thread_cleanup_is_pending

    if thread_cleanup_is_pending(user_id, req.thread_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "thread_cleanup_pending", "message": "该会话正在清理，请稍后重试"},
        )
    _schedule_title_generation(user_id, req.thread_id, req.message)
    steam_id = _steam_id_for_user(user_id, current_user, req.steam_id)
    started = time.perf_counter()
    run = _create_chat_run(req, user_id=user_id, steam_id=steam_id, started=started)

    return StreamingResponse(
        _chat_run_stream(run, include_snapshot=False),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/runs/{thread_id}")
async def attach_chat_run(
    thread_id: str = Path(min_length=1, max_length=128),
    current_user: str = Depends(require_user),
):
    run = _find_chat_run(current_user, thread_id)
    if run is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return StreamingResponse(
        _chat_run_stream(run),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/runs/{thread_id}/cancel")
async def cancel_chat_run(
    thread_id: str = Path(min_length=1, max_length=128),
    current_user: str = Depends(require_user),
):
    run = _find_chat_run(current_user, thread_id)
    if run is None or run.state != "running" or run.task is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "thread_run_not_active", "message": "该会话没有正在生成的回答"},
        )
    run.task.cancel()
    return {"status": "cancelling"}


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
        active_threads = _active_chat_threads(user_id)
        for thread in threads:
            thread["is_running"] = thread["thread_id"] in active_threads
        return {"threads": threads}
    # fallback to old message-only list
    raw = get_thread_list(user_id)
    active_threads = _active_chat_threads(user_id)
    for thread in raw:
        thread["is_running"] = thread["thread_id"] in active_threads
    return {"threads": raw}


@router.post("/thread-title")
async def set_title(
    payload: ThreadTitleRequest,
    current_user: str = Depends(require_user),
):
    from ..memory.thread_title import set_thread_title
    from ..memory.message_store import thread_belongs_to_user

    if not thread_belongs_to_user(current_user, payload.thread_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "thread_not_found", "message": "会话不存在或无权操作"},
        )
    title = payload.title.strip()
    if not title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_title", "message": "会话标题不能为空"},
        )
    set_thread_title(current_user, payload.thread_id, title)
    return {"status": "ok"}


@router.get("/messages")
async def read_messages(
    thread_id: str = Query(..., min_length=1, max_length=128),
    user_id: str | None = Query(default=None, include_in_schema=False),
    current_user: str = Depends(require_user),
):
    user_id = _identity(current_user, user_id or "")
    from ..memory.message_store import thread_belongs_to_user

    if not thread_belongs_to_user(user_id, thread_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "thread_not_found", "message": "会话不存在或无权操作"},
        )
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
    run = _find_chat_run(user_id, thread_id)
    if run is not None and run.state == "running" and run.task is not None:
        run.task.cancel()
        try:
            await run.task
        except asyncio.CancelledError:
            pass
    result = await asyncio.to_thread(delete_thread_detailed, user_id, thread_id)
    if result["status"] == "not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "thread_not_found", "message": "会话不存在或无权操作"},
        )
    partial = result["status"] == "partial"
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED if partial else status.HTTP_200_OK,
        content={
            "status": "partial" if partial else "ok",
            "message": "会话已删除，部分后台清理待补偿" if partial else "会话已删除",
            "layers": result["layers"],
            "pending_layers": result.get("pending_layers", []),
        },
    )


@router.post("/threads/{thread_id}/cleanup/retry")
async def retry_thread_delete_cleanup(
    thread_id: str = Path(min_length=1, max_length=128),
    current_user: str = Depends(require_user),
):
    from ..memory.message_store import retry_thread_cleanup

    result = await asyncio.to_thread(retry_thread_cleanup, current_user, thread_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "cleanup_not_pending", "message": "该会话没有待重试的清理任务"},
        )
    partial = result["status"] == "partial"
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED if partial else status.HTTP_200_OK,
        content={
            "status": "partial" if partial else "ok",
            "layers": result["layers"],
            "pending_layers": result.get("pending_layers", []),
        },
    )


# ── Steam ID 绑定 API ──

@router.post("/bind-steam")
async def bind_steam(
    payload: SteamBindRequest,
    current_user: str = Depends(require_user),
):
    """绑定 Steam ID：auth 表 + user_insights 表 + 预热画像。"""
    from ..memory.game_profile import get_game_profile

    # Each account has one Steam ID; multiple accounts may share that ID.
    user_id = current_user
    steam_id = payload.steam_id
    ok, msg = bind_steam_id(user_id, steam_id)
    if not ok:
        if "频繁" in msg:
            code, http_status = "rate_limited", status.HTTP_429_TOO_MANY_REQUESTS
        elif "用户不存在" in msg:
            code, http_status = "user_not_found", status.HTTP_404_NOT_FOUND
        else:
            code, http_status = "invalid_steam_id", status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=http_status, detail={"code": code, "message": msg})

    # Warm profile cache
    try:
        profile = await asyncio.wait_for(
            asyncio.to_thread(get_game_profile, steam_id),
            timeout=STEAM_PROFILE_WARMUP_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "steam_profile_warmup_timeout",
            extra={"fields": {"timeout_seconds": STEAM_PROFILE_WARMUP_TIMEOUT_SECONDS}},
        )
        profile = ""
    except Exception as exc:
        logger.warning("steam_profile_warmup_failed", extra={"error_type": type(exc).__name__})
        profile = ""

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
    if not ok:
        http_status = status.HTTP_429_TOO_MANY_REQUESTS if "频繁" in msg else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=http_status, detail={"code": "registration_failed", "message": msg})
    return {"status": "ok", "message": msg, "username": payload.username.strip()}


@router.post("/auth/login")
async def auth_login(payload: AuthRequest, response: Response, request: Request):
    client_host = request.client.host if request.client else ""
    ok, msg = login(
        payload.username,
        payload.password,
        rate_limit_key=client_host or None,
    )
    if not ok:
        http_status = status.HTTP_429_TOO_MANY_REQUESTS if "频繁" in msg else status.HTTP_401_UNAUTHORIZED
        code = "rate_limited" if http_status == status.HTTP_429_TOO_MANY_REQUESTS else "invalid_credentials"
        raise HTTPException(status_code=http_status, detail={"code": code, "message": msg})
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
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "user_not_found", "message": "用户不存在"},
    )


def _maybe_generate_title(user_id: str, thread_id: str, message: str):
    """Auto-generate a short title for a new thread based on the first user message."""
    try:
        from ..memory.thread_title import auto_generate_title
        auto_generate_title(user_id, thread_id, message)
    except Exception:
        pass
