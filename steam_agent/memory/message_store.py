"""SQLite message store for exact conversation history lookup.

Table: messages
  - user_id + thread_id together isolate conversations
  - turn_number tracks order within a session
  - role: 'user' or 'assistant'
"""

import sqlite3

from ..config import SQLITE_DB_PATH


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_messages_table():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            turn_number INTEGER NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_user_thread
        ON messages(user_id, thread_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_user_time
        ON messages(user_id, created_at DESC)
    """)
    conn.commit()
    conn.close()


def add_message(user_id: str, thread_id: str, turn_number: int, role: str, content: str):
    """Insert one message row. Called at the end of each turn."""
    init_messages_table()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO messages (user_id, thread_id, turn_number, role, content) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, thread_id, turn_number, role, content),
    )
    conn.commit()
    conn.close()


def get_thread_messages(user_id: str, thread_id: str) -> list[dict]:
    """Get all messages for a specific thread, ordered by turn."""
    init_messages_table()
    conn = _get_conn()
    cursor = conn.execute(
        "SELECT turn_number, role, content, created_at "
        "FROM messages WHERE user_id = ? AND thread_id = ? "
        "ORDER BY turn_number",
        (user_id, thread_id),
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"turn": r[0], "role": r[1], "content": r[2], "time": r[3]} for r in rows]


def get_thread_list(user_id: str) -> list[dict]:
    """List all threads for a user, sorted by most recent first."""
    init_messages_table()
    conn = _get_conn()
    cursor = conn.execute(
        "SELECT thread_id, MAX(created_at) as last_active, "
        "COUNT(*) as msg_count "
        "FROM messages WHERE user_id = ? "
        "GROUP BY thread_id ORDER BY last_active DESC",
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"thread_id": r[0], "last_active": r[1], "msg_count": r[2]} for r in rows]


def get_messages_by_turn(user_id: str, thread_id: str, turn_number: int | None = None,
                         role: str | None = None) -> list[dict]:
    """Query messages with optional turn/role filters. Used by Agent tool."""
    init_messages_table()
    conn = _get_conn()
    query = "SELECT turn_number, role, content, created_at FROM messages WHERE user_id = ? AND thread_id = ?"
    params: list = [user_id, thread_id]

    if turn_number is not None:
        query += " AND turn_number = ?"
        params.append(turn_number)
    if role is not None:
        query += " AND role = ?"
        params.append(role)

    query += " ORDER BY turn_number"
    cursor = conn.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [{"turn": r[0], "role": r[1], "content": r[2], "time": r[3]} for r in rows]


def delete_thread(user_id: str, thread_id: str) -> bool:
    """Delete a thread and all its data from all 4 storage layers.

    Returns True if at least one message record was found and deleted.
    """
    import sqlite3 as _sqlite3

    deleted_any = False

    # ── Layer 1: messages (SQLite) ──
    init_messages_table()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id = ? AND thread_id = ?",
            (user_id, thread_id),
        )
        if cur.fetchone()[0] > 0:
            conn.execute(
                "DELETE FROM messages WHERE user_id = ? AND thread_id = ?",
                (user_id, thread_id),
            )
            conn.commit()
            deleted_any = True
    finally:
        conn.close()

    # ── Layer 2: threads_meta (SQLite) ──
    try:
        from .thread_title import init_threads_table
        init_threads_table()
        conn2 = _get_conn()
        conn2.execute(
            "DELETE FROM threads_meta WHERE user_id = ? AND thread_id = ?",
            (user_id, thread_id),
        )
        conn2.commit()
        conn2.close()
    except Exception:
        pass

    # ── Layer 3: LangGraph checkpoints (checkpoints.db) ──
    try:
        from ..config import CHECKPOINT_DB_PATH
        cp_conn = _sqlite3.connect(CHECKPOINT_DB_PATH)
        cp_conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
        cp_conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
        cp_conn.commit()
        cp_conn.close()
    except Exception:
        pass

    # ── Layer 4: ChromaDB semantic memory ──
    try:
        from ..rag.vector_store import get_user_memory_collection
        collection = get_user_memory_collection()
        collection.delete(where={"thread_id": thread_id})
    except Exception:
        pass

    return deleted_any
