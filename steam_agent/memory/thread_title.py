"""Thread metadata: auto-generated titles, user-editable."""

import sqlite3

from ..config import SQLITE_DB_PATH


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
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (thread_id, user_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_threads_user_time
        ON threads_meta(user_id, updated_at DESC)
    """)
    conn.commit()
    conn.close()


def set_thread_title(user_id: str, thread_id: str, title: str):
    """Insert or update thread title."""
    init_threads_table()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO threads_meta (thread_id, user_id, title, updated_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(thread_id, user_id) DO UPDATE SET title = excluded.title, updated_at = datetime('now')",
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
    try:
        from langchain_openai import ChatOpenAI
        from ..config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

        llm = ChatOpenAI(
            model="deepseek-chat", temperature=0.0, max_tokens=32,
            api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL,
        )
        prompt = (
            f"将以下用户的第一句话概括为6个字以内的会话标题，只输出标题本身不要解释：\n\n"
            f"用户: {user_message[:200]}"
        )
        resp = llm.invoke(prompt)
        title = resp.content.strip().replace('"','').replace('"','').replace('《','').replace('》','')
        if not title or len(title) > 20:
            title = user_message[:15]
        set_thread_title(user_id, thread_id, title)
        return title
    except Exception:
        title = user_message[:15] + ("..." if len(user_message) > 15 else "")
        set_thread_title(user_id, thread_id, title)
        return title
