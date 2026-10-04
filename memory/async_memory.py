"""Durable asynchronous extraction of explicitly requested user memories."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

from config import (
    MEMORY_AGENT_LEASE_SECONDS,
    MEMORY_AGENT_MAX_ATTEMPTS,
    MEMORY_AGENT_MIN_CONFIDENCE,
    MEMORY_AGENT_RETRY_BASE_SECONDS,
    SQLITE_DB_PATH,
)
from model_routing import select_model
from observability import metrics
from memory.insight_store import save_insight


logger = logging.getLogger(__name__)

_EXPLICIT_MEMORY_PATTERN = re.compile(
    r"记住|记得|牢记|以后|今后|将来|我的偏好|我喜欢|我偏好|我不喜欢|我讨厌|不要给我|别推荐|预算|主要使用|常用平台",
    re.IGNORECASE,
)
_VALID_ACTIONS = {"add", "replace", "delete"}
_VALID_CATEGORIES = {"preference", "constraint", "fact"}
_VALID_SCOPES = {"stable", "temporary", "session"}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def _transaction():
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_memory_tasks_table(conn: sqlite3.Connection) -> None:
    """Create the queue schema using the caller's transaction when possible."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            turn_number INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'retry', 'completed', 'failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at REAL NOT NULL DEFAULT 0,
            lease_until REAL,
            result_json TEXT NOT NULL DEFAULT '{}',
            review_json TEXT NOT NULL DEFAULT '{}',
            last_error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(user_id, thread_id, turn_number)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_tasks_ready "
        "ON memory_tasks(status, available_at, updated_at)"
    )


def init_memory_tasks_table() -> None:
    with _transaction() as conn:
        create_memory_tasks_table(conn)


def enqueue_memory_extraction(
    conn: sqlite3.Connection,
    user_id: str,
    thread_id: str,
    turn_number: int,
) -> None:
    """Queue extraction atomically with the archived conversation turn."""
    now = time.time()
    create_memory_tasks_table(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO memory_tasks
            (user_id, thread_id, turn_number, status, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', ?, ?)
        """,
        (user_id, thread_id, turn_number, now, now),
    )


def claim_memory_tasks(
    limit: int = 10,
    *,
    now: float | None = None,
    lease_seconds: float = MEMORY_AGENT_LEASE_SECONDS,
) -> list[dict[str, Any]]:
    """Claim ready or expired tasks so another worker can safely retry them."""
    now = time.time() if now is None else float(now)
    limit = max(1, min(int(limit), 100))
    lease_until = now + max(1.0, float(lease_seconds))
    with _transaction() as conn:
        create_memory_tasks_table(conn)
        rows = conn.execute(
            """
            SELECT id, user_id, thread_id, turn_number, attempts
            FROM memory_tasks
            WHERE (status IN ('pending', 'retry') AND available_at <= ?)
               OR (status = 'running' AND lease_until IS NOT NULL AND lease_until < ?)
            ORDER BY id
            LIMIT ?
            """,
            (now, now, limit),
        ).fetchall()
        claimed: list[dict[str, Any]] = []
        for row in rows:
            attempts = int(row["attempts"]) + 1
            conn.execute(
                """
                UPDATE memory_tasks
                SET status='running', attempts=?, lease_until=?, updated_at=?, last_error=''
                WHERE id=?
                """,
                (attempts, lease_until, now, row["id"]),
            )
            claimed.append({
                "id": int(row["id"]),
                "user_id": row["user_id"],
                "thread_id": row["thread_id"],
                "turn_number": int(row["turn_number"]),
                "attempts": attempts,
            })
        return claimed


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def complete_memory_task(task_id: int, result: dict, review: dict) -> None:
    now = time.time()
    with _transaction() as conn:
        conn.execute(
            """
            UPDATE memory_tasks
            SET status='completed', lease_until=NULL, result_json=?, review_json=?,
                last_error='', updated_at=?
            WHERE id=? AND status='running'
            """,
            (_json(result), _json(review), now, task_id),
        )


def fail_memory_task(task_id: int, attempts: int, error: str) -> str:
    now = time.time()
    if attempts >= MEMORY_AGENT_MAX_ATTEMPTS:
        next_status = "failed"
        available_at = now
    else:
        next_status = "retry"
        delay = MEMORY_AGENT_RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1))
        available_at = now + min(delay, 3600)
    with _transaction() as conn:
        conn.execute(
            """
            UPDATE memory_tasks
            SET status=?, available_at=?, lease_until=NULL, last_error=?, updated_at=?
            WHERE id=? AND status='running'
            """,
            (next_status, available_at, str(error)[:1000], now, task_id),
        )
    return next_status


def get_memory_task(task_id: int) -> dict[str, Any] | None:
    init_memory_tasks_table()
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM memory_tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content or "")


def _parse_operations(text: str) -> list[dict[str, Any]]:
    cleaned = text.strip().replace("```json", "").replace("```", "").strip()
    candidates: Any = None
    for start in (cleaned.find("["), cleaned.find("{")):
        if start < 0:
            continue
        try:
            candidates = json.loads(cleaned[start:])
            break
        except json.JSONDecodeError:
            continue
    if isinstance(candidates, dict):
        candidates = candidates.get("operations", candidates.get("memories", []))
    return [item for item in candidates or [] if isinstance(item, dict)][:5]


def _review_operations(user_message: str, raw_operations: list[dict[str, Any]]) -> tuple[list[dict], dict]:
    """Apply a conservative server-side quality gate before persistence."""
    if not _EXPLICIT_MEMORY_PATTERN.search(user_message):
        return [], {"approved": 0, "rejected": len(raw_operations), "reason": "no_explicit_memory_intent"}

    accepted: list[dict] = []
    rejected: list[str] = []
    for index, item in enumerate(raw_operations):
        action = str(item.get("action", "add")).strip().lower()
        category = str(item.get("category", "fact")).strip().lower()
        scope = str(item.get("scope", "stable")).strip().lower()
        memory_key = str(item.get("memory_key", "")).strip()
        value = str(item.get("value", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        try:
            confidence = float(item.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if action not in _VALID_ACTIONS or category not in _VALID_CATEGORIES or scope not in _VALID_SCOPES:
            rejected.append(f"{index}:invalid_enum")
            continue
        if not 0 < confidence <= 1 or confidence < MEMORY_AGENT_MIN_CONFIDENCE:
            rejected.append(f"{index}:low_confidence")
            continue
        if not 1 <= len(memory_key) <= 200 or len(value) > 2000:
            rejected.append(f"{index}:invalid_length")
            continue
        if action != "delete" and not value:
            rejected.append(f"{index}:missing_value")
            continue
        if action == "delete":
            value = ""
        if not evidence or evidence not in user_message:
            rejected.append(f"{index}:evidence_not_in_user_message")
            continue
        ttl_days = item.get("ttl_days")
        if scope != "stable" and ttl_days is not None:
            try:
                ttl_days = int(ttl_days)
            except (TypeError, ValueError):
                rejected.append(f"{index}:invalid_ttl")
                continue
            if not 1 <= ttl_days <= 3650:
                rejected.append(f"{index}:invalid_ttl")
                continue
        accepted.append({
            "memory_key": memory_key,
            "value": value,
            "category": category,
            "action": action,
            "confidence": confidence,
            "scope": scope,
            "ttl_days": ttl_days,
            "evidence": evidence,
        })
    return accepted, {"approved": len(accepted), "rejected": len(rejected), "rejections": rejected}


def extract_memory_operations(user_message: str, assistant_reply: str) -> tuple[list[dict], dict]:
    """Ask a small model for candidates, then review them using user text only."""
    from langchain_openai import ChatOpenAI
    from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, LLM_MAX_RETRIES, LLM_REQUEST_TIMEOUT_SECONDS

    selection = select_model("memory")
    llm = ChatOpenAI(
        model=selection.model,
        temperature=0.0,
        max_tokens=512,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        timeout=LLM_REQUEST_TIMEOUT_SECONDS,
        max_retries=LLM_MAX_RETRIES,
    )
    prompt = (
        "你是长期记忆提取器。只从用户原话提取明确要求以后记住的稳定偏好、限制或事实。"
        "用户没有明确表达记忆意图时返回空数组；不要把助手内容当作事实。"
        "只输出 JSON 数组，每项字段为 memory_key,value,category,action,confidence,scope,ttl_days,evidence。"
        "action 只能是 add/replace/delete，category 只能是 preference/constraint/fact，"
        "scope 只能是 stable/temporary/session；evidence 必须是用户原话中的连续短句。"
        f"\n用户原话：{user_message[:6000]}\n助手回答（仅供上下文，不可作为证据）：{assistant_reply[:2000]}"
    )
    raw = _parse_operations(_extract_text(llm.invoke(prompt)))
    accepted, review = _review_operations(user_message, raw)
    review["model"] = selection.model
    return accepted, review


def process_memory_task(task: dict[str, Any]) -> str:
    from memory.message_store import get_messages_by_turn
    from memory.message_store import thread_belongs_to_user

    messages = get_messages_by_turn(task["user_id"], task["thread_id"], task["turn_number"])
    user_message = next((item["content"] for item in messages if item["role"] == "user"), "")
    assistant_reply = next((item["content"] for item in messages if item["role"] == "assistant"), "")
    if not user_message or not assistant_reply:
        complete_memory_task(task["id"], {"saved": 0}, {"approved": 0, "rejected": 0, "reason": "turn_not_found"})
        return "completed"
    if not _EXPLICIT_MEMORY_PATTERN.search(user_message):
        complete_memory_task(
            task["id"],
            {"saved": 0},
            {"approved": 0, "rejected": 0, "reason": "no_explicit_memory_intent"},
        )
        metrics.increment("memory_agent_tasks_total", status="completed")
        return "completed"
    if not thread_belongs_to_user(task["user_id"], task["thread_id"]):
        metrics.increment("memory_agent_tasks_total", status="cancelled")
        return "cancelled"

    operations, review = extract_memory_operations(user_message, assistant_reply)
    saved: list[dict] = []
    for operation in operations:
        result = save_insight(
            task["user_id"],
            operation["value"],
            operation["category"],
            action=operation["action"],
            confidence=operation["confidence"],
            source="async_memory_agent",
            scope=operation["scope"],
            ttl_days=operation["ttl_days"],
            memory_key=operation["memory_key"],
        )
        saved.append({
            "memory_key": operation["memory_key"],
            "action": operation["action"],
            "status": result.get("status", "unknown"),
        })
    complete_memory_task(task["id"], {"saved": len(saved), "operations": saved}, review)
    metrics.increment("memory_agent_tasks_total", status="completed")
    return "completed"


def run_pending_memory_tasks(limit: int = 10) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for task in claim_memory_tasks(limit):
        try:
            outcome = process_memory_task(task)
            outcomes.append({"id": task["id"], "status": outcome})
        except Exception as exc:
            status = fail_memory_task(task["id"], task["attempts"], f"{type(exc).__name__}: {exc}")
            metrics.increment("memory_agent_tasks_total", status=status)
            logger.warning(
                "memory_agent_task_failed",
                extra={"fields": {"task_id": task["id"], "status": status, "error_type": type(exc).__name__}},
            )
            outcomes.append({"id": task["id"], "status": status})
    return outcomes
