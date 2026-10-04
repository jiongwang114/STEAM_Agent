"""Thread metadata: auto-generated titles, user-editable."""

import sqlite3

from config import SQLITE_DB_PATH


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_threads_table():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS threads_meta (
            thread_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '新会话',
            title_source TEXT NOT NULL DEFAULT 'auto',
            auto_title_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (thread_id, user_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_threads_user_time
        ON threads_meta(user_id, updated_at DESC)
    """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(threads_meta)")}
    if "title_source" not in columns:
        conn.execute(
            "ALTER TABLE threads_meta ADD COLUMN title_source TEXT NOT NULL DEFAULT 'auto'"
        )
        conn.execute(
            "UPDATE threads_meta SET title_source='manual' WHERE title != '新会话'"
        )
    if "auto_title_status" not in columns:
        conn.execute(
            "ALTER TABLE threads_meta ADD COLUMN auto_title_status TEXT NOT NULL DEFAULT 'pending'"
        )
        conn.execute(
            "UPDATE threads_meta SET auto_title_status='done' WHERE title_source='manual' OR title != '新会话'"
        )
    conn.commit()
    conn.close()


def set_thread_title(user_id: str, thread_id: str, title: str):
    """Set a manual title that automatic generation can never overwrite."""
    init_threads_table()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO threads_meta (thread_id, user_id, title, title_source, auto_title_status, updated_at) "
        "VALUES (?, ?, ?, 'manual', 'done', datetime('now')) "
        "ON CONFLICT(thread_id, user_id) DO UPDATE SET title = excluded.title, "
        "title_source='manual', auto_title_status='done', updated_at=datetime('now')",
        (thread_id, user_id, title[:50]),
    )
    conn.commit()
    conn.close()


def get_thread_title(user_id: str, thread_id: str) -> str:
    """Get title, default '新会话'."""
    init_threads_table()
    conn = _get_conn()
    row = conn.execute(
        "SELECT title FROM threads_meta WHERE user_id = ? AND thread_id = ?",
        (user_id, thread_id),
    ).fetchone()
    conn.close()
    return row[0] if row else "新会话"


def delete_thread_title(user_id: str, thread_id: str) -> None:
    init_threads_table()
    conn = _get_conn()
    conn.execute(
        "DELETE FROM threads_meta WHERE user_id=? AND thread_id=?",
        (user_id, thread_id),
    )
    conn.commit()
    conn.close()


def claim_auto_title_generation(user_id: str, thread_id: str) -> bool:
    """Create or atomically claim the one automatic title task for a thread."""
    init_threads_table()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO threads_meta "
            "(thread_id, user_id, title, title_source, auto_title_status) "
            "VALUES (?, ?, '新会话', 'auto', 'pending')",
            (thread_id, user_id),
        )
        updated = conn.execute(
            "UPDATE threads_meta SET auto_title_status='running', updated_at=datetime('now') "
            "WHERE user_id=? AND thread_id=? AND title_source='auto' "
            "AND title='新会话' AND (auto_title_status='pending' OR "
            "(auto_title_status='running' AND updated_at < datetime('now', '-10 minutes')))",
            (user_id, thread_id),
        ).rowcount
        conn.commit()
        return updated == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _finish_auto_title(user_id: str, thread_id: str, title: str) -> bool:
    init_threads_table()
    conn = _get_conn()
    try:
        updated = conn.execute(
            "UPDATE threads_meta SET title=?, auto_title_status='done', updated_at=datetime('now') "
            "WHERE user_id=? AND thread_id=? AND title_source='auto' "
            "AND auto_title_status='running'",
            (title[:50], user_id, thread_id),
        ).rowcount
        conn.commit()
        return updated == 1
    finally:
        conn.close()


def _auto_title_is_running(user_id: str, thread_id: str) -> bool:
    init_threads_table()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM threads_meta WHERE user_id=? AND thread_id=? "
            "AND title_source='auto' AND auto_title_status='running'",
            (user_id, thread_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_thread_list_with_titles(user_id: str) -> list[dict]:
    """Get thread list with titles, fallback to message count from messages table."""
    init_threads_table()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT tm.thread_id, tm.title, tm.updated_at, "
        "  (SELECT COUNT(*) FROM messages m WHERE m.user_id = tm.user_id AND m.thread_id = tm.thread_id) as msg_count "
        "FROM threads_meta tm WHERE tm.user_id = ? ORDER BY tm.updated_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [{"thread_id": r[0], "title": r[1], "last_active": r[2], "msg_count": r[3]} for r in rows]


def auto_generate_title(user_id: str, thread_id: str, user_message: str) -> str:
    """Use LLM to generate a short title from the first user message.
    Falls back to first 20 chars of the message."""
    if not _auto_title_is_running(user_id, thread_id) and not claim_auto_title_generation(user_id, thread_id):
        return get_thread_title(user_id, thread_id)
    try:
        from llm_client import create_chat_model
        from config import (
            DEEPSEEK_API_KEY,
            DEEPSEEK_BASE_URL,
            LLM_MAX_RETRIES,
            LLM_REQUEST_TIMEOUT_SECONDS,
        )
        from model_routing import select_model

        llm = create_chat_model(
            model=select_model("title").model, temperature=0.0, max_tokens=32,
            api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL,
            timeout=LLM_REQUEST_TIMEOUT_SECONDS, max_retries=LLM_MAX_RETRIES,
        )
        prompt = (
            f"将以下用户的第一句话概括为6个字以内的会话标题，只输出标题本身不要解释：\n\n"
            f"用户: {user_message[:200]}"
        )
        resp = llm.invoke(prompt)
        title = resp.content.strip().replace('"','').replace('"','').replace('《','').replace('》','')
        if not title or len(title) > 20:
            title = user_message[:15]
        _finish_auto_title(user_id, thread_id, title)
        return title
    except Exception:
        title = user_message[:15] + ("..." if len(user_message) > 15 else "")
        _finish_auto_title(user_id, thread_id, title)
        return title
