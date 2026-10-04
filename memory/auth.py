"""Password authentication and server-side sessions.

The database stores only Argon2id password hashes and SHA-256 hashes of
random session tokens. Raw session tokens exist only in the browser cookie.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError

from config import (
    AUTH_RATE_LIMIT_MAX_FAILURES,
    AUTH_RATE_LIMIT_WINDOW_SECONDS,
    SESSION_TTL_SECONDS,
    SQLITE_DB_PATH,
)


_PASSWORD_HASHER = PasswordHasher()
_USERNAME_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{2,64}$")
_STEAM_ID_PATTERN = re.compile(r"^\d{17}$")


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


def init_auth_table() -> None:
    with _transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                bound_steam_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                theme TEXT NOT NULL DEFAULT 'dark'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                revoked_at REAL,
                FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(username, expires_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_attempts (
                attempt_key TEXT PRIMARY KEY,
                failures INTEGER NOT NULL DEFAULT 0,
                window_started_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                action TEXT NOT NULL,
                success INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # Migrate existing databases: a Steam profile may be shared by accounts.
        conn.execute("DROP INDEX IF EXISTS idx_users_steam_unique")


def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not _USERNAME_PATTERN.fullmatch(username):
        raise ValueError("用户名长度需为 2-64 个字符，且不能包含控制字符")
    return username


def validate_steam_id(steam_id: str) -> str:
    steam_id = (steam_id or "").strip()
    if not _STEAM_ID_PATTERN.fullmatch(steam_id):
        raise ValueError("Steam ID 必须是 17 位数字")
    return steam_id


def _hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def _legacy_sha256(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _verify_password(stored_hash: str, password: str) -> tuple[bool, bool]:
    """Return (valid, needs_upgrade), accepting legacy SHA-256 once."""
    if re.fullmatch(r"[0-9a-f]{64}", stored_hash or ""):
        return secrets.compare_digest(stored_hash, _legacy_sha256(password)), True
    try:
        return (
            _PASSWORD_HASHER.verify(stored_hash, password),
            _PASSWORD_HASHER.check_needs_rehash(stored_hash),
        )
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False, False


def _record_attempt(conn: sqlite3.Connection, key: str, success: bool) -> bool:
    now = time.time()
    row = conn.execute(
        "SELECT failures, window_started_at FROM auth_attempts WHERE attempt_key=?",
        (key,),
    ).fetchone()
    in_window = row and now - float(row["window_started_at"]) < AUTH_RATE_LIMIT_WINDOW_SECONDS
    failures = int(row["failures"]) if in_window else 0
    if success:
        conn.execute("DELETE FROM auth_attempts WHERE attempt_key=?", (key,))
        return True
    failures += 1
    conn.execute(
        "INSERT INTO auth_attempts(attempt_key, failures, window_started_at) VALUES(?,?,?) "
        "ON CONFLICT(attempt_key) DO UPDATE SET failures=excluded.failures, window_started_at=excluded.window_started_at",
        (key, failures, float(row["window_started_at"]) if in_window else now),
    )
    return failures <= AUTH_RATE_LIMIT_MAX_FAILURES


def register(username: str, password: str) -> tuple[bool, str]:
    try:
        username = validate_username(username)
    except ValueError as exc:
        return False, str(exc)
    if not password or len(password) < 8 or len(password) > 256:
        return False, "密码长度需为 8-256 个字符"
    init_auth_table()
    try:
        with _transaction() as conn:
            rate_key = f"register:{username.lower()}"
            if not _record_attempt(conn, rate_key, success=False):
                return False, "注册尝试过于频繁，请稍后再试"
            if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                return False, "用户名或密码不可用"
            conn.execute(
                "INSERT INTO users(username, password_hash) VALUES(?, ?)",
                (username, _hash_password(password)),
            )
            conn.execute(
                "INSERT INTO auth_audit(username, action, success) VALUES(?,?,1)",
                (username, "register"),
            )
            _record_attempt(conn, rate_key, success=True)
    except sqlite3.IntegrityError:
        return False, "用户名或密码不可用"
    return True, "注册成功"


def login(
    username: str,
    password: str,
    *,
    rate_limit_key: str | None = None,
) -> tuple[bool, str]:
    username = (username or "").strip()
    init_auth_table()
    keys = [f"account:{username.lower()}"]
    if rate_limit_key:
        keys.append(f"ip:{rate_limit_key}")
    with _transaction() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username=?", (username,)
        ).fetchone()
        allowed = True
        for key in keys:
            allowed = _record_attempt(conn, key, success=False) and allowed
        if not allowed:
            conn.execute(
                "INSERT INTO auth_audit(username, action, success) VALUES(?,?,0)",
                (username or None, "login_rate_limited"),
            )
            return False, "登录尝试过于频繁，请稍后再试"
        valid, needs_upgrade = (
            _verify_password(row["password_hash"], password)
            if row
            else (False, False)
        )
        if not valid:
            conn.execute(
                "INSERT INTO auth_audit(username, action, success) VALUES(?,?,0)",
                (username or None, "login"),
            )
            return False, "用户名或密码错误"
        if needs_upgrade:
            conn.execute(
                "UPDATE users SET password_hash=? WHERE username=?",
                (_hash_password(password), username),
            )
        for key in keys:
            _record_attempt(conn, key, success=True)
        conn.execute(
            "INSERT INTO auth_audit(username, action, success) VALUES(?,?,1)",
            (username, "login"),
        )
    return True, "登录成功"


def create_session(username: str) -> str:
    username = validate_username(username)
    init_auth_table()
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    now = time.time()
    with _transaction() as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash, username, created_at, expires_at, last_seen_at) VALUES(?,?,?,?,?)",
            (token_hash, username, now, now + SESSION_TTL_SECONDS, now),
        )
    return raw_token


def get_session_user(raw_token: str | None) -> str | None:
    if not raw_token or len(raw_token) > 512:
        return None
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = time.time()
    init_auth_table()
    with _transaction() as conn:
        row = conn.execute(
            "SELECT username, expires_at, revoked_at FROM sessions WHERE token_hash=?",
            (token_hash,),
        ).fetchone()
        if not row or row["revoked_at"] is not None or float(row["expires_at"]) <= now:
            return None
        conn.execute(
            "UPDATE sessions SET last_seen_at=? WHERE token_hash=?",
            (now, token_hash),
        )
        return str(row["username"])


def revoke_session(raw_token: str | None) -> None:
    if not raw_token:
        return
    init_auth_table()
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    with _transaction() as conn:
        conn.execute(
            "UPDATE sessions SET revoked_at=? WHERE token_hash=?",
            (time.time(), token_hash),
        )


def revoke_all_sessions(username: str) -> None:
    init_auth_table()
    with _transaction() as conn:
        conn.execute(
            "UPDATE sessions SET revoked_at=? WHERE username=? AND revoked_at IS NULL",
            (time.time(), username),
        )


def get_user_info(username: str) -> dict | None:
    init_auth_table()
    conn = _get_conn()
    row = conn.execute(
        "SELECT username, bound_steam_id, created_at FROM users WHERE username=?",
        (username,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "username": row["username"],
        "bound_steam_id": row["bound_steam_id"] or "",
        "created_at": row["created_at"],
    }


def bind_steam_id(username: str, steam_id: str) -> tuple[bool, str]:
    try:
        steam_id = validate_steam_id(steam_id)
    except ValueError as exc:
        return False, str(exc)
    init_auth_table()
    from memory.insight_store import init_db as init_insight_db, sync_bound_steam_id

    init_insight_db()
    with _transaction() as conn:
        rate_key = f"bind:{username.lower()}"
        if not _record_attempt(conn, rate_key, success=False):
            return False, "绑定尝试过于频繁，请稍后再试"
        if not conn.execute(
            "SELECT 1 FROM users WHERE username=?", (username,)
        ).fetchone():
            return False, "用户不存在"
        conn.execute(
            "UPDATE users SET bound_steam_id=? WHERE username=?",
            (steam_id, username),
        )
        sync_bound_steam_id(conn, username, steam_id)
        conn.execute(
            "INSERT INTO auth_audit(username, action, success) VALUES(?,?,1)",
            (username, "bind_steam"),
        )
        _record_attempt(conn, rate_key, success=True)
    return True, "Steam ID 绑定成功"


