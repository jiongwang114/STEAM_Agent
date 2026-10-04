"""FastAPI authentication dependencies and request identity helpers."""

from __future__ import annotations

import json

from fastapi import Cookie, Header, HTTPException, status

from config import SESSION_COOKIE_NAME
from memory.auth import get_session_user


def require_user(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> str:
    token = session_cookie
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    username = get_session_user(token)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "authentication_required", "message": "请先登录"},
        )
    return username


def direct_call_user(current_user, requested_user: str | None) -> str:
    """Keep direct Python calls usable in tests without weakening HTTP auth."""
    if isinstance(current_user, str):
        return current_user
    return (requested_user or "").strip()


def qualified_thread_id(user_id: str, thread_id: str) -> str:
    """Stable checkpoint key that cannot collide across users."""
    return "steam-thread:" + json.dumps(
        [user_id, thread_id], ensure_ascii=False, separators=(",", ":")
    )
