"""SQLite persistence for compressed conversation summaries."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from config import SQLITE_DB_PATH


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_session_summaries_table() -> None:
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            covered_from_turn INTEGER NOT NULL,
            covered_to_turn INTEGER NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, thread_id, version)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_summaries_latest "
        "ON session_summaries(user_id, thread_id, version DESC)"
    )
    conn.commit()
    conn.close()


def get_latest_session_summary(user_id: str, thread_id: str) -> dict | None:
    if not user_id or not thread_id:
        return None
    init_session_summaries_table()
    conn = _get_conn()
    row = conn.execute(
        "SELECT version, covered_from_turn, covered_to_turn, summary, created_at, updated_at "
        "FROM session_summaries WHERE user_id=? AND thread_id=? "
        "ORDER BY version DESC LIMIT 1",
        (user_id, thread_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_session_summary(
    user_id: str,
    thread_id: str,
    version: int,
    covered_from_turn: int,
    covered_to_turn: int,
    summary: str,
) -> dict:
    if not user_id or not thread_id or not summary.strip():
        raise ValueError("user_id, thread_id and summary are required")
    if version < 1 or covered_from_turn < 1 or covered_to_turn < covered_from_turn:
        raise ValueError("invalid session summary metadata")
    init_session_summaries_table()
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO session_summaries "
        "(user_id, thread_id, version, covered_from_turn, covered_to_turn, summary, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, thread_id, version) DO UPDATE SET "
        "covered_from_turn=excluded.covered_from_turn, "
        "covered_to_turn=excluded.covered_to_turn, summary=excluded.summary, "
        "updated_at=excluded.updated_at",
        (
            user_id,
            thread_id,
            version,
            covered_from_turn,
            covered_to_turn,
            summary.strip(),
            now,
        ),
    )
    conn.commit()
    conn.close()
    return {
        "version": version,
        "covered_from_turn": covered_from_turn,
        "covered_to_turn": covered_to_turn,
        "summary": summary.strip(),
    }


def delete_session_summaries(user_id: str, thread_id: str) -> None:
    init_session_summaries_table()
    conn = _get_conn()
    conn.execute(
        "DELETE FROM session_summaries WHERE user_id=? AND thread_id=?",
        (user_id, thread_id),
    )
    conn.commit()
    conn.close()
