import sqlite3
from datetime import datetime, timezone

from ..config import SQLITE_DB_PATH


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            insight TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('preference', 'constraint', 'fact')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_insights_user_id
        ON user_insights(user_id)
    """)
    conn.commit()
    conn.close()


def add_insight(user_id: str, insight: str, category: str):
    init_db()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO user_insights (user_id, insight, category) VALUES (?, ?, ?)",
        (user_id, insight, category),
    )
    conn.commit()
    conn.close()


def remove_insight(user_id: str, insight: str):
    init_db()
    conn = _get_conn()
    conn.execute(
        "DELETE FROM user_insights WHERE user_id = ? AND insight = ?",
        (user_id, insight),
    )
    conn.commit()
    conn.close()


def get_insights(user_id: str) -> list[dict]:
    init_db()
    conn = _get_conn()
    cursor = conn.execute(
        "SELECT insight, category, created_at FROM user_insights WHERE user_id = ? "
        "ORDER BY created_at DESC",
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"insight": r[0], "category": r[1], "created_at": r[2]} for r in rows]
