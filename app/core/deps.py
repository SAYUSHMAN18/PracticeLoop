from __future__ import annotations

from fastapi import Depends, Request

from app.core.db import get_pool
from app.core.security import current_user_id


class LoginRequired(Exception):
    """Raised by require_user_id; handled by an app-level exception handler
    that redirects to /login (see app/main.py)."""


async def require_user_id(request: Request) -> int:
    user_id = current_user_id(request)
    if user_id is None:
        raise LoginRequired()
    return user_id


async def inject_current_user(request: Request, pool=Depends(get_pool)) -> None:
    """Router-level dependency (see dashboard/practice/jobs/documents/profile
    routers) that makes the logged-in user's name/email available to every
    template as request.state.current_user, without every single route
    handler having to fetch and pass it through its own context dict --
    base.html's sidebar reads it directly off `request`, which Jinja2Templates
    already injects into every render.

    A single indexed primary-key lookup, not worth threading through
    asyncio.gather at each call site for -- if this ever shows up as real
    latency, thread it through instead of guessing."""
    user_id = current_user_id(request)
    if user_id is None:
        request.state.current_user = None
        request.state.current_streak = None
        request.state.current_xp = None
        return

    from app.auth.service import get_user  # local import: avoids a core -> auth import at module load time

    request.state.current_user = await get_user(pool, user_id)

    # The Phase 5 topbar shows the streak on every page, not just the
    # dashboard -- same "just query it, thread it through gather if it
    # ever shows up as real latency" tradeoff as the user lookup above.
    # local import: avoids a core -> practice import at module load time
    from app.practice.service import streak_days

    request.state.current_streak = await streak_days(pool, user_id)

    # Phase 10's XP/level badge, same topbar-on-every-page treatment.
    from app.gamification.service import get_xp_summary

    request.state.current_xp = await get_xp_summary(pool, user_id)
