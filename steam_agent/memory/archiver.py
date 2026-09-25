"""Recoverable SQLite + Chroma conversation archiving."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..rag.embedder import embed_memory
from ..rag.vector_store import get_user_memory_collection
from ..observability import metrics
from .message_store import (
    archive_sqlite_turn,
    get_pending_archive_tasks,
    update_archive_task,
)


logger = logging.getLogger(__name__)


def archive_conversation(
    user_id: str,
    thread_id: str,
    user_message: str,
    assistant_reply: str,
    turn_number: int | None = None,
) -> dict:
    """Persist a turn and make its semantic copy, reporting partial failure.

    SQLite allocates the turn number so restarts cannot reuse a turn.
    """
    if not user_message.strip() or not assistant_reply.strip():
        metrics.increment("archive_tasks_total", status="skipped")
        return {"status": "skipped", "turn_number": None, "layers": {}}

    timestamp = datetime.now(timezone.utc).isoformat()
    task = archive_sqlite_turn(
        user_id,
        thread_id,
        user_message,
        assistant_reply,
        timestamp,
    )
    if task["status"] == "complete":
        metrics.increment("archive_tasks_total", status="complete")
        return {
            "status": "complete",
            "turn_number": task["turn_number"],
            "layers": {"sqlite": "ok", "semantic_memory": "ok"},
        }

    try:
        text = f"User: {user_message}\nAssistant: {assistant_reply}"
        collection = get_user_memory_collection()
        collection.upsert(
            ids=[task["task_id"]],
            embeddings=embed_memory([text]),
            documents=[text],
            metadatas=[{
                "user_id": user_id,
                "thread_id": thread_id,
                "timestamp": timestamp,
                "turn_number": task["turn_number"],
            }],
        )
        update_archive_task(task["task_id"], "complete")
        metrics.increment("archive_tasks_total", status="complete")
        return {
            "status": "complete",
            "turn_number": task["turn_number"],
            "layers": {"sqlite": "ok", "semantic_memory": "ok"},
        }
    except Exception as exc:
        update_archive_task(task["task_id"], "failed", f"{type(exc).__name__}: {exc}")
        metrics.increment("archive_tasks_total", status="partial")
        logger.warning(
            "archive_semantic_write_failed",
            extra={"fields": {
                "user_id": user_id,
                "thread_id": thread_id,
                "turn_number": task["turn_number"],
                "error_type": type(exc).__name__,
            }},
        )
        return {
            "status": "partial",
            "turn_number": task["turn_number"],
            "layers": {"sqlite": "ok", "semantic_memory": "pending"},
            "task_id": task["task_id"],
        }


def retry_pending_archives(limit: int = 100) -> dict:
    """Replay failed semantic writes from SQLite's authoritative task table."""
    completed = 0
    failed = 0
    for task in get_pending_archive_tasks(limit):
        try:
            text = f"User: {task['user_message']}\nAssistant: {task['assistant_reply']}"
            get_user_memory_collection().upsert(
                ids=[task["task_id"]],
                embeddings=embed_memory([text]),
                documents=[text],
                metadatas=[{
                    "user_id": task["user_id"],
                    "thread_id": task["thread_id"],
                    "timestamp": task["timestamp"],
                    "turn_number": task["turn_number"],
                }],
            )
            update_archive_task(task["task_id"], "complete")
            metrics.increment("archive_tasks_total", status="recovered")
            completed += 1
        except Exception as exc:
            update_archive_task(task["task_id"], "failed", f"{type(exc).__name__}: {exc}")
            metrics.increment("archive_tasks_total", status="failed")
            failed += 1
    return {"attempted": completed + failed, "completed": completed, "failed": failed}
