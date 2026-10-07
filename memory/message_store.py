"""SQLite conversation archive and durable turn allocation."""

from __future__ import annotations

import sqlite3
import json
import logging
from contextlib import contextmanager
from threading import Lock

from config import SQLITE_DB_PATH


logger = logging.getLogger(__name__)


# The API request and the background retry worker can observe the same task.
# Serialize cleanup so legacy Chroma deletion is not run concurrently.
_THREAD_CLEANUP_LOCK = Lock()
_SCHEMA_READY = False


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
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


def init_messages_table() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                turn_number INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_user_thread "
            "ON messages(user_id, thread_id, turn_number)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_user_time "
            "ON messages(user_id, created_at DESC)"
        )
        conn.execute(
            "DELETE FROM messages WHERE id NOT IN ("
            "SELECT MIN(id) FROM messages GROUP BY user_id, thread_id, turn_number, role)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_turn_role "
            "ON messages(user_id, thread_id, turn_number, role)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS thread_counters (
                user_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                next_turn INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(user_id, thread_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS thread_cleanup_tasks (
                user_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                pending_layers_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY(user_id, thread_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_summaries (
                user_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                turn_number INTEGER NOT NULL,
                summary_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY(user_id, thread_id, turn_number)
            )
            """
        )
        from memory.async_memory import create_memory_tasks_table

        create_memory_tasks_table(conn)
    _SCHEMA_READY = True


def reserve_turn_number(user_id: str, thread_id: str) -> int:
    """Allocate the next turn atomically across processes."""
    init_messages_table()
    with _transaction() as conn:
        pending_cleanup = conn.execute(
            "SELECT 1 FROM thread_cleanup_tasks WHERE user_id=? AND thread_id=?",
            (user_id, thread_id),
        ).fetchone()
        if pending_cleanup:
            raise RuntimeError("thread deletion cleanup is pending")
        row = conn.execute(
            "SELECT next_turn FROM thread_counters WHERE user_id=? AND thread_id=?",
            (user_id, thread_id),
        ).fetchone()
        if row is None:
            max_row = conn.execute(
                "SELECT COALESCE(MAX(turn_number), 0) AS value FROM messages "
                "WHERE user_id=? AND thread_id=?",
                (user_id, thread_id),
            ).fetchone()
            turn = int(max_row["value"]) + 1
            conn.execute(
                "INSERT INTO thread_counters(user_id, thread_id, next_turn) VALUES(?,?,?)",
                (user_id, thread_id, turn + 1),
            )
            return turn
        turn = int(row["next_turn"])
        conn.execute(
            "UPDATE thread_counters SET next_turn=? WHERE user_id=? AND thread_id=?",
            (turn + 1, user_id, thread_id),
        )
        return turn


def archive_sqlite_turn(
    user_id: str,
    thread_id: str,
    user_message: str,
    assistant_reply: str,
    execution: dict | None = None,
) -> dict:
    """Write the authoritative raw turn to SQLite in one transaction."""
    init_messages_table()
    with _transaction() as conn:
        pending_cleanup = conn.execute(
            "SELECT 1 FROM thread_cleanup_tasks WHERE user_id=? AND thread_id=?",
            (user_id, thread_id),
        ).fetchone()
        if pending_cleanup:
            raise RuntimeError("thread deletion cleanup is pending")
        row = conn.execute(
            "SELECT next_turn FROM thread_counters WHERE user_id=? AND thread_id=?",
            (user_id, thread_id),
        ).fetchone()
        if row is None:
            max_row = conn.execute(
                "SELECT COALESCE(MAX(turn_number), 0) AS value FROM messages "
                "WHERE user_id=? AND thread_id=?",
                (user_id, thread_id),
            ).fetchone()
            turn_number = int(max_row["value"]) + 1
        else:
            turn_number = int(row["next_turn"])
        conn.execute(
            "INSERT INTO thread_counters(user_id, thread_id, next_turn) VALUES(?,?,?) "
            "ON CONFLICT(user_id, thread_id) DO UPDATE SET next_turn=excluded.next_turn",
            (user_id, thread_id, turn_number + 1),
        )
        conn.executemany(
            "INSERT INTO messages(user_id, thread_id, turn_number, role, content) VALUES(?,?,?,?,?)",
            [
                (user_id, thread_id, turn_number, "user", user_message),
                (user_id, thread_id, turn_number, "assistant", assistant_reply),
            ],
        )
        if execution:
            conn.execute(
                "INSERT INTO execution_summaries(user_id, thread_id, turn_number, summary_json) "
                "VALUES(?,?,?,?)",
                (user_id, thread_id, turn_number, json.dumps(_public_execution(execution), ensure_ascii=False)),
            )
        from memory.async_memory import enqueue_memory_extraction
        enqueue_memory_extraction(conn, user_id, thread_id, turn_number)
        return {"turn_number": turn_number, "status": "complete"}


def thread_belongs_to_user(user_id: str, thread_id: str) -> bool:
    """Check thread ownership against archived messages and thread metadata."""
    if not user_id or not thread_id:
        return False
    init_messages_table()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE user_id=? AND thread_id=? LIMIT 1",
            (user_id, thread_id),
        ).fetchone()
        if row:
            return True
        try:
            row = conn.execute(
                "SELECT 1 FROM threads_meta WHERE user_id=? AND thread_id=? LIMIT 1",
                (user_id, thread_id),
            ).fetchone()
            return row is not None
        except sqlite3.OperationalError:
            return False
    finally:
        conn.close()


def thread_cleanup_is_pending(user_id: str, thread_id: str) -> bool:
    if not user_id or not thread_id:
        return False
    init_messages_table()
    conn = _get_conn()
    try:
        return conn.execute(
            "SELECT 1 FROM thread_cleanup_tasks WHERE user_id=? AND thread_id=?",
            (user_id, thread_id),
        ).fetchone() is not None
    finally:
        conn.close()


def add_message(user_id: str, thread_id: str, turn_number: int, role: str, content: str):
    if role not in {"user", "assistant"}:
        raise ValueError("role must be user or assistant")
    init_messages_table()
    with _transaction() as conn:
        conn.execute(
            "INSERT INTO messages(user_id, thread_id, turn_number, role, content) "
            "VALUES(?,?,?,?,?)",
            (user_id, thread_id, turn_number, role, content),
        )


def _public_execution(execution: dict) -> dict:
    """Keep only the user-facing execution fields in durable history."""
    if not isinstance(execution, dict):
        return {}
    steps = []
    for item in execution.get("steps") or []:
        if not isinstance(item, dict):
            continue
        step = {
            "name": str(item.get("name", ""))[:100],
            "status": str(item.get("status", "unknown"))[:32],
            "duration_ms": max(0, int(item.get("duration_ms", 0) or 0)),
        }
        if item.get("round") is not None:
            step["round"] = max(0, int(item["round"]))
        if step["name"]:
            steps.append(step)
    return {
        "status": str(execution.get("status", "success"))[:32],
        "duration_ms": max(0, int(execution.get("duration_ms", 0) or 0)),
        "steps": steps,
    }


def _message_payload(row: sqlite3.Row) -> dict:
    payload = {
        "turn": row["turn_number"],
        "role": row["role"],
        "content": row["content"],
        "time": row["created_at"],
    }
    if row["role"] == "assistant" and row["summary_json"]:
        try:
            payload["execution"] = _public_execution(json.loads(row["summary_json"]))
        except (TypeError, json.JSONDecodeError):
            pass
    return payload


def get_thread_messages(user_id: str, thread_id: str) -> list[dict]:
    init_messages_table()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT m.turn_number, m.role, m.content, m.created_at, e.summary_json "
        "FROM messages m LEFT JOIN execution_summaries e "
        "ON e.user_id=m.user_id AND e.thread_id=m.thread_id AND e.turn_number=m.turn_number "
        "WHERE m.user_id=? AND m.thread_id=? ORDER BY m.turn_number, m.id",
        (user_id, thread_id),
    ).fetchall()
    conn.close()
    return [_message_payload(row) for row in rows]


def get_thread_list(user_id: str) -> list[dict]:
    init_messages_table()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT thread_id, MAX(created_at) AS last_active, COUNT(*) AS msg_count "
        "FROM messages WHERE user_id=? GROUP BY thread_id ORDER BY last_active DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [
        {"thread_id": row["thread_id"], "last_active": row["last_active"], "msg_count": row["msg_count"]}
        for row in rows
    ]


def get_messages_by_turn(
    user_id: str,
    thread_id: str,
    turn_number: int | None = None,
    role: str | None = None,
) -> list[dict]:
    init_messages_table()
    conn = _get_conn()
    query = (
        "SELECT m.turn_number, m.role, m.content, m.created_at, e.summary_json "
        "FROM messages m LEFT JOIN execution_summaries e "
        "ON e.user_id=m.user_id AND e.thread_id=m.thread_id AND e.turn_number=m.turn_number "
        "WHERE m.user_id=? AND m.thread_id=?"
    )
    params: list = [user_id, thread_id]
    if turn_number is not None:
        query += " AND m.turn_number=?"
        params.append(turn_number)
    if role is not None:
        query += " AND m.role=?"
        params.append(role)
    query += " ORDER BY m.turn_number, m.id"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_message_payload(row) for row in rows]


_THREAD_CLEANUP_LAYERS = (
    "sqlite",
    "session_summaries",
    "thread_title",
    "legacy_vector_cleanup",
    "checkpoints",
)


def _delete_legacy_thread_vectors(collection, user_id: str, thread_id: str) -> None:
    """Delete only matching legacy records without relying on Chroma `$and` syntax."""
    try:
        payload = collection.get(where={"user_id": user_id}, include=["metadatas"])
    except ValueError:
        payload = collection.get(where={"thread_id": thread_id}, include=["metadatas"])

    ids = payload.get("ids", [])
    metadatas = payload.get("metadatas", [])
    matching_ids = [
        record_id
        for record_id, metadata in zip(ids, metadatas)
        if isinstance(metadata, dict)
        and str(metadata.get("user_id", "")) == user_id
        and str(metadata.get("thread_id", "")) == thread_id
    ]
    if matching_ids:
        collection.delete(ids=matching_ids)


def _store_pending_cleanup(user_id: str, thread_id: str, pending: list[str], error: str = "") -> None:
    with _transaction() as conn:
        if pending:
            conn.execute(
                "UPDATE thread_cleanup_tasks SET pending_layers_json=?, attempts=attempts+1, "
                "last_error=?, updated_at=datetime('now') WHERE user_id=? AND thread_id=?",
                (json.dumps(pending), error[:1000], user_id, thread_id),
            )
        else:
            conn.execute(
                "DELETE FROM thread_cleanup_tasks WHERE user_id=? AND thread_id=?",
                (user_id, thread_id),
            )


def _run_thread_cleanup(user_id: str, thread_id: str, pending: list[str]) -> dict:
    with _THREAD_CLEANUP_LOCK:
        # A worker may have read a task just before an API request finished it.
        # Re-read the durable task while holding the lock so stale work cannot
        # recreate a cleanup task after the request has completed.
        current_pending = _pending_for_thread(user_id, thread_id)
        if not current_pending:
            return {"status": "deleted", "layers": {}, "pending_layers": []}
        return _run_thread_cleanup_locked(user_id, thread_id, current_pending)


def _run_thread_cleanup_locked(user_id: str, thread_id: str, pending: list[str]) -> dict:
    from api.security import qualified_thread_id
    from config import CHECKPOINT_DB_PATH

    result = {"status": "partial", "layers": {}}
    failures: list[str] = []
    remaining = list(pending)
    for layer in pending:
        try:
            if layer == "sqlite":
                with _transaction() as conn:
                    conn.execute(
                        "DELETE FROM messages WHERE user_id=? AND thread_id=?",
                        (user_id, thread_id),
                    )
                    conn.execute(
                        "DELETE FROM execution_summaries WHERE user_id=? AND thread_id=?",
                        (user_id, thread_id),
                    )
                    conn.execute(
                        "DELETE FROM memory_tasks WHERE user_id=? AND thread_id=?",
                        (user_id, thread_id),
                    )
                    conn.execute(
                        "DELETE FROM thread_counters WHERE user_id=? AND thread_id=?",
                        (user_id, thread_id),
                    )
                    legacy_tasks = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive_tasks'"
                    ).fetchone()
                    if legacy_tasks:
                        conn.execute(
                            "DELETE FROM archive_tasks WHERE user_id=? AND thread_id=?",
                            (user_id, thread_id),
                        )
            elif layer == "session_summaries":
                from memory.session_summary import delete_session_summaries

                delete_session_summaries(user_id, thread_id)
            elif layer == "thread_title":
                from memory.thread_title import delete_thread_title

                delete_thread_title(user_id, thread_id)
            elif layer == "legacy_vector_cleanup":
                from rag.vector_store import get_legacy_user_memory_collection

                collection = get_legacy_user_memory_collection()
                if collection is not None:
                    _delete_legacy_thread_vectors(collection, user_id, thread_id)
            elif layer == "checkpoints":
                from pathlib import Path

                checkpoint_path = Path(CHECKPOINT_DB_PATH)
                if checkpoint_path.exists():
                    cp_conn = sqlite3.connect(str(checkpoint_path), timeout=30.0)
                    try:
                        tables = {
                            row[0]
                            for row in cp_conn.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            )
                        }
                        checkpoint_id = qualified_thread_id(user_id, thread_id)
                        if "writes" in tables:
                            cp_conn.execute(
                                "DELETE FROM writes WHERE thread_id=?", (checkpoint_id,)
                            )
                        if "checkpoints" in tables:
                            cp_conn.execute(
                                "DELETE FROM checkpoints WHERE thread_id=?", (checkpoint_id,)
                            )
                        cp_conn.commit()
                    finally:
                        cp_conn.close()
            else:
                raise ValueError(f"unknown cleanup layer: {layer}")
            result["layers"][layer] = "ok"
            remaining.remove(layer)
            _store_pending_cleanup(user_id, thread_id, remaining)
        except Exception as exc:
            result["layers"][layer] = f"error:{type(exc).__name__}"
            detail = str(exc).strip().replace("\n", " ")[:300]
            logger.warning(
                "thread_cleanup_layer_failed",
                extra={
                    "fields": {
                        "user_id": user_id,
                        "thread_id": thread_id,
                        "layer": layer,
                        "error_type": type(exc).__name__,
                        "error": detail,
                    }
                },
            )
            failures.append(f"{layer}:{type(exc).__name__}:{detail}")

    for layer in set(_THREAD_CLEANUP_LAYERS) - set(pending):
        result["layers"][layer] = "ok"
    if remaining:
        _store_pending_cleanup(user_id, thread_id, remaining, ",".join(failures))
    else:
        result["status"] = "deleted"
    result["pending_layers"] = remaining
    return result


def delete_thread_detailed(user_id: str, thread_id: str) -> dict:
    """Queue and perform idempotent cleanup, retaining failed layers for retry."""
    init_messages_table()
    with _transaction() as conn:
        task = conn.execute(
            "SELECT pending_layers_json FROM thread_cleanup_tasks WHERE user_id=? AND thread_id=?",
            (user_id, thread_id),
        ).fetchone()
        exists = conn.execute(
            "SELECT 1 FROM messages WHERE user_id=? AND thread_id=? LIMIT 1",
            (user_id, thread_id),
        ).fetchone()
        if not exists:
            has_metadata = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='threads_meta'"
            ).fetchone()
            if has_metadata:
                exists = conn.execute(
                    "SELECT 1 FROM threads_meta WHERE user_id=? AND thread_id=? LIMIT 1",
                    (user_id, thread_id),
                ).fetchone()
        if not task and not exists:
            return {"status": "not_found", "layers": {}, "pending_layers": []}
        if task:
            pending = json.loads(task["pending_layers_json"])
        else:
            pending = list(_THREAD_CLEANUP_LAYERS)
            conn.execute(
                "INSERT INTO thread_cleanup_tasks(user_id, thread_id, pending_layers_json) "
                "VALUES(?,?,?)",
                (user_id, thread_id, json.dumps(pending)),
            )
    return _run_thread_cleanup(user_id, thread_id, pending)


def retry_thread_cleanup(user_id: str, thread_id: str) -> dict | None:
    init_messages_table()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT pending_layers_json FROM thread_cleanup_tasks WHERE user_id=? AND thread_id=?",
            (user_id, thread_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _run_thread_cleanup(user_id, thread_id, json.loads(row["pending_layers_json"]))


def retry_pending_thread_deletions(limit: int = 25) -> list[dict]:
    init_messages_table()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT user_id, thread_id FROM thread_cleanup_tasks "
            "ORDER BY updated_at LIMIT ?",
            (max(1, min(int(limit), 250)),),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "user_id": row["user_id"],
            "thread_id": row["thread_id"],
            **_run_thread_cleanup(
                row["user_id"],
                row["thread_id"],
                _pending_for_thread(row["user_id"], row["thread_id"]),
            ),
        }
        for row in rows
    ]


def _pending_for_thread(user_id: str, thread_id: str) -> list[str]:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT pending_layers_json FROM thread_cleanup_tasks WHERE user_id=? AND thread_id=?",
            (user_id, thread_id),
        ).fetchone()
        return json.loads(row["pending_layers_json"]) if row else []
    finally:
        conn.close()


def delete_thread(user_id: str, thread_id: str) -> bool:
    """Backward-compatible boolean wrapper for internal callers."""
    result = delete_thread_detailed(user_id, thread_id)
    return result["status"] in {"deleted", "partial"}
