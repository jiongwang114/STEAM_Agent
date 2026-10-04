from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Literal

from config import SQLITE_DB_PATH


MemoryScope = Literal["stable", "temporary", "session"]
_VALID_CATEGORIES = {"preference", "constraint", "fact"}
_VALID_SCOPES = {"stable", "temporary", "session"}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
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
    additions = {
        "normalized_key": "TEXT NOT NULL DEFAULT ''",
        "polarity": "INTEGER NOT NULL DEFAULT 0",
        "confidence": "REAL NOT NULL DEFAULT 1.0",
        "source": "TEXT NOT NULL DEFAULT 'legacy'",
        "scope": "TEXT NOT NULL DEFAULT 'stable'",
        "expires_at": "TEXT",
        "updated_at": "TEXT NOT NULL DEFAULT ''",
        "active": "INTEGER NOT NULL DEFAULT 1",
        "superseded_by": "INTEGER",
    }
    existing = {row[1] for row in conn.execute("PRAGMA table_info(user_insights)")}
    for column, declaration in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE user_insights ADD COLUMN {column} {declaration}")
    legacy_rows = conn.execute(
        "SELECT id, insight, category, created_at FROM user_insights WHERE normalized_key=''"
    ).fetchall()
    for row in legacy_rows:
        conn.execute(
            "UPDATE user_insights SET normalized_key=?, polarity=?, updated_at=? WHERE id=?",
            (
                canonical_memory_key(row["insight"], row["category"]),
                memory_polarity(row["insight"]),
                row["created_at"],
                row["id"],
            ),
        )
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_insights_user_active
        ON user_insights(user_id, active, updated_at DESC)
    """)
    conn.commit()
    conn.close()


def save_insight(
    user_id: str,
    value: str,
    category: str,
    *,
    action: Literal["add", "replace", "delete"] = "add",
    confidence: float = 1.0,
    source: str = "explicit_user",
    scope: MemoryScope = "stable",
    ttl_days: int | None = None,
    memory_key: str | None = None,
) -> dict:
    """Apply one structured memory operation for one authenticated user."""
    if category not in _VALID_CATEGORIES:
        raise ValueError(f"invalid category: {category}")
    if scope not in _VALID_SCOPES:
        raise ValueError(f"invalid scope: {scope}")
    if action not in {"add", "replace", "delete"}:
        raise ValueError(f"invalid action: {action}")
    if not user_id:
        raise ValueError("user_id is required")
    value = str(value or "").strip()
    if action != "delete" and not value:
        raise ValueError("value is required")
    if len(value) > 2000:
        raise ValueError("value is too long")
    if not memory_key or not memory_key.strip():
        if action == "delete":
            raise ValueError("memory_key is required for delete")
        memory_key = canonical_memory_key(value, category)
    key = normalize_memory_key(memory_key)
    if len(key) > 200:
        raise ValueError("memory_key is too long")

    if action == "delete":
        return delete_insight(user_id, key)

    if scope != "stable" and ttl_days is not None and not 1 <= int(ttl_days) <= 3650:
        raise ValueError("ttl_days must be between 1 and 3650")

    confidence = max(0.0, min(float(confidence), 1.0))
    polarity = memory_polarity(value)
    now = datetime.now(timezone.utc)
    expires_at = _expiry(now, scope, ttl_days)

    init_db()
    conn = _get_conn()
    try:
        exact = conn.execute(
            "SELECT id FROM user_insights WHERE user_id=? AND normalized_key=? "
            "AND lower(trim(insight))=lower(trim(?)) AND active=1 "
            "ORDER BY id DESC LIMIT 1",
            (user_id, key, value),
        ).fetchone()
        if exact:
            conn.execute(
                "UPDATE user_insights SET insight=?, category=?, polarity=?, confidence=MAX(confidence, ?), "
                "source=?, scope=?, expires_at=?, updated_at=? WHERE id=?",
                (value, category, polarity, confidence, source, scope, expires_at, now.isoformat(), exact["id"]),
            )
            superseded: list[int] = []
            if action == "replace":
                rows = conn.execute(
                    "SELECT id FROM user_insights WHERE user_id=? AND normalized_key=? "
                    "AND active=1 AND id<>?",
                    (user_id, key, exact["id"]),
                ).fetchall()
                superseded = [int(row["id"]) for row in rows]
                if superseded:
                    conn.execute(
                        "UPDATE user_insights SET active=0, superseded_by=?, updated_at=? "
                        "WHERE user_id=? AND normalized_key=? AND active=1 AND id<>?",
                        (exact["id"], now.isoformat(), user_id, key, exact["id"]),
                    )
            conn.commit()
            return {
                "status": "superseded" if superseded else "deduplicated",
                "insight_id": exact["id"],
                "superseded": superseded,
            }

        cursor = conn.execute(
            "INSERT INTO user_insights "
            "(user_id, insight, category, normalized_key, polarity, confidence, source, "
            "scope, expires_at, updated_at, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (
                user_id, value, category, key, polarity, confidence, source,
                scope, expires_at, now.isoformat(),
            ),
        )
        insight_id = int(cursor.lastrowid)
        superseded: list[int] = []
        if action == "replace":
            rows = conn.execute(
                "SELECT id FROM user_insights WHERE user_id=? AND normalized_key=? "
                "AND active=1 AND id<>?",
                (user_id, key, insight_id),
            ).fetchall()
            superseded = [int(row["id"]) for row in rows]
            if superseded:
                placeholders = ",".join("?" for _ in superseded)
                conn.execute(
                    f"UPDATE user_insights SET active=0, superseded_by=?, updated_at=? "
                    f"WHERE id IN ({placeholders})",
                    (insight_id, now.isoformat(), *superseded),
                )
        conn.commit()
        return {
            "status": "superseded" if superseded else "inserted",
            "insight_id": insight_id,
            "superseded": superseded,
        }
    finally:
        conn.close()


def sync_bound_steam_id(conn: sqlite3.Connection, user_id: str, steam_id: str) -> None:
    """Keep the profile fact in the caller's SQLite transaction."""
    now = datetime.now(timezone.utc).isoformat()
    memory_key = "fact:steam_id"
    value = f"用户Steam ID: {steam_id}"
    current = conn.execute(
        "SELECT id FROM user_insights WHERE user_id=? AND normalized_key=? AND active=1 "
        "ORDER BY id DESC LIMIT 1",
        (user_id, memory_key),
    ).fetchone()
    if current:
        insight_id = int(current["id"])
        conn.execute(
            "UPDATE user_insights SET insight=?, category='fact', polarity=0, confidence=1.0, "
            "source='steam_binding', scope='stable', expires_at=NULL, updated_at=? WHERE id=?",
            (value, now, insight_id),
        )
    else:
        cursor = conn.execute(
            "INSERT INTO user_insights "
            "(user_id, insight, category, normalized_key, polarity, confidence, source, "
            "scope, expires_at, updated_at, active) "
            "VALUES (?, ?, 'fact', ?, 0, 1.0, 'steam_binding', 'stable', NULL, ?, 1)",
            (user_id, value, memory_key, now),
        )
        insight_id = int(cursor.lastrowid)

    conn.execute(
        "UPDATE user_insights SET active=0, superseded_by=?, updated_at=? "
        "WHERE user_id=? AND active=1 AND id<>? AND ("
        "normalized_key LIKE '%steamid%' OR lower(insight) LIKE '%steam id%' "
        "OR lower(insight) LIKE '%steamid%')",
        (insight_id, now, user_id, insight_id),
    )


def add_insight(
    user_id: str,
    insight: str,
    category: str,
    *,
    confidence: float = 1.0,
    source: str = "explicit_user",
    scope: MemoryScope = "stable",
    ttl_days: int | None = None,
    memory_key: str | None = None,
) -> dict:
    """Backward-compatible wrapper for callers that still use ``insight``."""
    return save_insight(
        user_id,
        insight,
        category,
        action="replace",
        confidence=confidence,
        source=source,
        scope=scope,
        ttl_days=ttl_days,
        memory_key=memory_key,
    )


def delete_insight(user_id: str, memory_key: str) -> dict:
    """Deactivate all active records for a stable memory key."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id FROM user_insights WHERE user_id=? AND normalized_key=? AND active=1",
            (user_id, normalize_memory_key(memory_key)),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            conn.execute(
                "UPDATE user_insights SET active=0, updated_at=? WHERE user_id=? "
                "AND normalized_key=? AND active=1",
                (now, user_id, normalize_memory_key(memory_key)),
            )
        conn.commit()
        return {"status": "deleted" if ids else "not_found", "memory_key": memory_key, "insight_ids": ids}
    finally:
        conn.close()


def remove_insight(user_id: str, insight: str) -> None:
    """Backward-compatible exact-value deactivation."""
    init_db()
    conn = _get_conn()
    conn.execute(
        "UPDATE user_insights SET active=0, updated_at=? WHERE user_id=? AND insight=? AND active=1",
        (datetime.now(timezone.utc).isoformat(), user_id, insight),
    )
    conn.commit()
    conn.close()


def get_insights(user_id: str, limit: int = 50) -> list[dict]:
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, insight, category, normalized_key, polarity, confidence, source, "
        "scope, expires_at, created_at, updated_at FROM user_insights "
        "WHERE user_id=? AND active=1 AND (expires_at IS NULL OR expires_at>?) "
        "ORDER BY confidence DESC, COALESCE(NULLIF(updated_at, ''), created_at) DESC LIMIT ?",
        (user_id, now, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def canonical_memory_key(insight: str, category: str) -> str:
    text = insight.lower().strip()
    text = re.sub(
        r"用户|我|现在|以后|一直|非常|很|开始|不再排斥|不排斥|不再喜欢|不喜欢|讨厌|喜欢|偏好|爱玩|只玩|想玩|预算|不超过|最多|限制",
        "",
        text,
    )
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return f"{category}:{text[:80]}"


def normalize_memory_key(memory_key: str) -> str:
    text = re.sub(r"\s+", " ", str(memory_key or "").strip().casefold())
    return re.sub(r"[^\w\u4e00-\u9fff :/-]+", "", text)


def memory_polarity(insight: str) -> int:
    text = insight.lower()
    if any(term in text for term in ("不再排斥", "不排斥", "开始喜欢")):
        return 1
    if any(term in text for term in ("不再喜欢", "不喜欢", "讨厌", "排斥")):
        return -1
    if any(term in text for term in ("喜欢", "偏好", "爱玩")):
        return 1
    return 0


def _expiry(now: datetime, scope: MemoryScope, ttl_days: int | None) -> str | None:
    if scope == "stable":
        return None
    if ttl_days is None:
        ttl_days = 7 if scope == "session" else 30 if scope == "temporary" else None
    return (now + timedelta(days=ttl_days)).isoformat() if ttl_days is not None else None
