"""User authentication: register, login, Steam ID binding.

Table: users
  - username: TEXT PRIMARY KEY (used as user_id)
  - password_hash: TEXT (sha256)
  - bound_steam_id: TEXT or NULL
  - created_at: TEXT
  - theme: TEXT default 'dark'
"""

import hashlib
import sqlite3

from ..config import SQLITE_DB_PATH


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_auth_table():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            bound_steam_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            theme TEXT NOT NULL DEFAULT 'dark'
        )
    """)
    conn.commit()
    conn.close()


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def register(username: str, password: str) -> tuple[bool, str]:
    """Register a new user. Returns (success, message)."""
    username = username.strip()
    if not username or len(username) < 2:
        return False, "用户名至少 2 个字符"
    if not password or len(password) < 3:
        return False, "密码至少 3 个字符"

    init_auth_table()
    conn = _get_conn()
    existing = conn.execute(
        "SELECT username FROM users WHERE username = ?", (username,)
    ).fetchone()
    if existing:
        conn.close()
        return False, "用户名已被占用"

    conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, _hash(password)),
    )
    conn.commit()
    conn.close()
    return True, "注册成功"


def login(username: str, password: str) -> tuple[bool, str]:
    """Login. Returns (success, message)."""
    init_auth_table()
    conn = _get_conn()
    row = conn.execute(
        "SELECT password_hash FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()

    if not row:
        return False, "用户不存在"
    if row[0] != _hash(password):
        return False, "密码错误"
    return True, "登录成功"


def get_user_info(username: str) -> dict | None:
    """Get user info dict: {username, bound_steam_id, theme, created_at}."""
    init_auth_table()
    conn = _get_conn()
    row = conn.execute(
        "SELECT username, bound_steam_id, theme, created_at FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "username": row[0],
        "bound_steam_id": row[1] or "",
        "theme": row[2] or "dark",
        "created_at": row[3],
    }


def bind_steam_id(username: str, steam_id: str) -> tuple[bool, str]:
    """Bind a Steam ID to a user. One Steam ID per user. Returns (success, message)."""
    init_auth_table()
    conn = _get_conn()

    # Check if steam_id is already bound to another user
    existing = conn.execute(
        "SELECT username FROM users WHERE bound_steam_id = ? AND username != ?",
        (steam_id, username),
    ).fetchone()
    if existing:
        conn.close()
        return False, f"此 Steam ID 已被用户 {existing[0]} 绑定"

    conn.execute(
        "UPDATE users SET bound_steam_id = ? WHERE username = ?",
        (steam_id, username),
    )
    conn.commit()
    conn.close()
    return True, "Steam ID 绑定成功"


def update_theme(username: str, theme: str):
    init_auth_table()
    conn = _get_conn()
    conn.execute("UPDATE users SET theme = ? WHERE username = ?", (theme, username))
    conn.commit()
    conn.close()
