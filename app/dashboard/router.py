from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.db import get_pool
from app.core.deps import require_user_id
from app.dashboard import service
from app.practice.service import streak_days

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    stats = await service.get_stats(pool, user_id)
    streak = await streak_days(pool, user_id)
    return templates.TemplateResponse(
        request, "dashboard/index.html", {"stats": stats, "streak": streak}
    )
