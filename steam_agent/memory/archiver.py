"""Authoritative SQLite-only conversation archiving."""

from __future__ import annotations

from ..observability import metrics
from .message_store import archive_sqlite_turn


def archive_conversation(
    user_id: str,
    thread_id: str,
    user_message: str,
    assistant_reply: str,
    turn_number: int | None = None,
    execution: dict | None = None,
) -> dict:
    """Persist one complete user/assistant turn without semantic duplication."""
    if not user_message.strip() or not assistant_reply.strip():
        metrics.increment("conversation_archive_total", status="skipped")
        return {"status": "skipped", "turn_number": None, "layers": {}}

    task = archive_sqlite_turn(
        user_id,
        thread_id,
        user_message,
        assistant_reply,
        execution=execution,
    )
    metrics.increment("conversation_archive_total", status="complete")
    return {
        "status": "complete",
        "turn_number": task["turn_number"],
        "layers": {"sqlite": "ok"},
    }
