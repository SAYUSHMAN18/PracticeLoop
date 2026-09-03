"""The operator dashboard, gated by ADMIN_EMAILS.

Admin isn't a role in the DB -- it's an env var list of email addresses
(app.core.config.admin_emails). Nothing in the product grants or revokes
it, there's no "make admin" button to get wrong, and a fresh clone with
no ADMIN_EMAILS set has no admin at all. The page is read-only: it
changes nothing, it just shows the operator what's going on.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.admin import service
from app.core.config import settings
from app.core.db import get_pool
from app.core.deps import LoginRequired, inject_current_user, require_user_id
from app.core.templates import templates

router = APIRouter(dependencies=[Depends(inject_current_user)])


def _admin_emails() -> set[str]:
    return {e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()}


async def require_admin(request: Request, user_id: int = Depends(require_user_id)) -> int:
    cu = request.state.current_user
    if cu is None or cu["email"].strip().lower() not in _admin_emails():
        # Same redirect as "not logged in" -- an admin URL shouldn't even
        # confirm it exists to a signed-in non-admin.
        raise LoginRequired()
    return user_id


@router.get("/admin", response_class=HTMLResponse)
async def admin_home(
    request: Request,
    _uid: int = Depends(require_admin),
    pool=Depends(get_pool),
):
    overview = await service.overview(pool)
    llm = await service.llm_stats(pool)
    signups = await service.recent_signups(pool)
    return templates.TemplateResponse(
        request,
        "admin/index.html",
        {"overview": overview, "llm": llm, "signups": signups, "email": service.email_status()},
    )
