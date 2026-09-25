"""SQLite conversation archive and durable turn allocation."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from ..config import SQLITE_DB_PATH


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
            CREATE TABLE IF NOT EXISTS archive_tasks (
                task_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                turn_number INTEGER NOT NULL,
                user_message TEXT NOT NULL,
                assistant_reply TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, thread_id, turn_number)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_archive_tasks_status "
            "ON archive_tasks(status, updated_at)"
        )


def reserve_turn_number(user_id: str, thread_id: str) -> int:
    """Allocate the next turn atomically across processes."""
    init_messages_table()
    with _transaction() as conn:
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
    timestamp: str,
) -> dict:
    """Write both messages and a pending Chroma task in one SQLite transaction."""
    init_messages_table()
    with _transaction() as conn:
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
        task_id = f"{user_id}:{thread_id}:{turn_number}"
        existing = conn.execute(
            "SELECT task_id, turn_number, status FROM archive_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if existing:
            return {
                "task_id": existing["task_id"],
                "turn_number": int(existing["turn_number"]),
                "status": existing["status"],
            }
        conn.executemany(
            "INSERT INTO messages(user_id, thread_id, turn_number, role, content) VALUES(?,?,?,?,?)",
            [
                (user_id, thread_id, turn_number, "user", user_message),
                (user_id, thread_id, turn_number, "assistant", assistant_reply),
            ],
        )
        conn.execute(
            """
            INSERT INTO archive_tasks(
                task_id, user_id, thread_id, turn_number,
                user_message, assistant_reply, timestamp
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                task_id,
                user_id,
                thread_id,
                turn_number,
                user_message,
                assistant_reply,
                timestamp,
            ),
        )
        return {"task_id": task_id, "turn_number": turn_number, "status": "pending"}


def update_archive_task(task_id: str, status: str, error: str = "") -> None:
    init_messages_table()
    with _transaction() as conn:
        conn.execute(
            "UPDATE archive_tasks SET status=?, attempts=attempts+1, last_error=?, "
            "updated_at=datetime('now') WHERE task_id=?",
            (status, error[:1000], task_id),
        )


def get_pending_archive_tasks(limit: int = 100) -> list[dict]:
    init_messages_table()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT task_id, user_id, thread_id, turn_number, user_message, "
        "assistant_reply, timestamp, status, attempts, last_error "
        "FROM archive_tasks WHERE status IN ('pending','failed') "
        "ORDER BY turn_number LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


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


def get_thread_messages(user_id: str, thread_id: str) -> list[dict]:
    init_messages_table()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT turn_number, role, content, created_at FROM messages "
        "WHERE user_id=? AND thread_id=? ORDER BY turn_number, id",
        (user_id, thread_id),
    ).fetchall()
    conn.close()
    return [
        {"turn": row["turn_number"], "role": row["role"], "content": row["content"], "time": row["created_at"]}
        for row in rows
    ]


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
        "SELECT turn_number, role, content, created_at FROM messages "
        "WHERE user_id=? AND thread_id=?"
    )
    params: list = [user_id, thread_id]
    if turn_number is not None:
        query += " AND turn_number=?"
        params.append(turn_number)
    if role is not None:
        query += " AND role=?"
        params.append(role)
    query += " ORDER BY turn_number, id"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [
        {"turn": row["turn_number"], "role": row["role"], "content": row["content"], "time": row["created_at"]}
        for row in rows
    ]


def get_all_conversation_turns() -> list[dict]:
    init_messages_table()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT user_id, thread_id, turn_number, "
        "MAX(CASE WHEN role='user' THEN content END) AS user_message, "
        "MAX(CASE WHEN role='assistant' THEN content END) AS assistant_reply, "
        "MAX(created_at) AS created_at FROM messages "
        "GROUP BY user_id, thread_id, turn_number "
        "HAVING user_message IS NOT NULL AND assistant_reply IS NOT NULL "
        "ORDER BY user_id, thread_id, turn_number"
    ).fetchall()
    conn.close()
    return [
        {
            "user_id": row["user_id"],
            "thread_id": row["thread_id"],
            "turn_number": row["turn_number"],
            "user_message": row["user_message"],
            "assistant_reply": row["assistant_reply"],
            "timestamp": row["created_at"],
        }
        for row in rows
    ]


def delete_thread_detailed(user_id: str, thread_id: str) -> dict:
    """Delete only the authenticated user's thread and report every layer."""
    import sqlite3 as raw_sqlite

    init_messages_table()
    result = {"status": "not_found", "layers": {}}
    conn = _get_conn()
    try:
        exists = conn.execute(
            "SELECT 1 FROM messages WHERE user_id=? AND thread_id=? LIMIT 1",
            (user_id, thread_id),
        ).fetchone()
        if not exists:
            exists = conn.execute(
                "SELECT 1 FROM archive_tasks WHERE user_id=? AND thread_id=? LIMIT 1",
                (user_id, thread_id),
            ).fetchone()
        result["status"] = "deleted" if exists else "not_found"
        conn.execute("DELETE FROM messages WHERE user_id=? AND thread_id=?", (user_id, thread_id))
        conn.execute("DELETE FROM archive_tasks WHERE user_id=? AND thread_id=?", (user_id, thread_id))
        conn.execute("DELETE FROM thread_counters WHERE user_id=? AND thread_id=?", (user_id, thread_id))
        conn.commit()
        result["layers"]["sqlite"] = "ok"
    except Exception as exc:
        conn.rollback()
        result["layers"]["sqlite"] = f"error:{type(exc).__name__}"
        result["status"] = "partial"
    finally:
        conn.close()

    try:
        from .thread_title import delete_thread_title
        delete_thread_title(user_id, thread_id)
        result["layers"]["thread_title"] = "ok"
    except Exception as exc:
        result["layers"]["thread_title"] = f"error:{type(exc).__name__}"
        result["status"] = "partial"

    try:
        from ..api.security import qualified_thread_id
        from ..config import CHECKPOINT_DB_PATH
        cp_conn = raw_sqlite.connect(CHECKPOINT_DB_PATH, timeout=30.0)
        checkpoint_id = qualified_thread_id(user_id, thread_id)
        cp_conn.execute("DELETE FROM writes WHERE thread_id=?", (checkpoint_id,))
        cp_conn.execute("DELETE FROM checkpoints WHERE thread_id=?", (checkpoint_id,))
        cp_conn.commit()
        cp_conn.close()
        result["layers"]["checkpoints"] = "ok"
    except Exception as exc:
        result["layers"]["checkpoints"] = f"error:{type(exc).__name__}"
        result["status"] = "partial"

    try:
        from ..rag.vector_store import get_user_memory_collection
        collection = get_user_memory_collection()
        collection.delete(where={"$and": [{"user_id": user_id}, {"thread_id": thread_id}]})
        result["layers"]["semantic_memory"] = "ok"
    except Exception as exc:
        result["layers"]["semantic_memory"] = f"error:{type(exc).__name__}"
        result["status"] = "partial"
    return result


def delete_thread(user_id: str, thread_id: str) -> bool:
    """Backward-compatible boolean wrapper for internal callers."""
    result = delete_thread_detailed(user_id, thread_id)
    return result["status"] in {"deleted", "partial"}
