from __future__ import annotations

from fastapi import Request

from app.core.security import current_user_id


class LoginRequired(Exception):
    """Raised by require_user_id; handled by an app-level exception handler
    that redirects to /login (see app/main.py)."""


async def require_user_id(request: Request) -> int:
    user_id = current_user_id(request)
    if user_id is None:
        raise LoginRequired()
    return user_id
