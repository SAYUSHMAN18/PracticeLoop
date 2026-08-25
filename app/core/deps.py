from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from starlette.status import HTTP_303_SEE_OTHER
from starlette.responses import RedirectResponse

from app.core.db import get_pool
from app.core.security import current_user_id


class RequireLoginRedirect(Exception):
    """Raised to signal the caller should redirect to /login."""


async def require_user_id(request: Request) -> int:
    user_id = current_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user_id
