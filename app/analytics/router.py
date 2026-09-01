from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.analytics import service
from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates
from app.dashboard.service import activity_last_7_days, topic_mastery

router = APIRouter(dependencies=[Depends(inject_current_user)])


@router.get("/progress", response_class=HTMLResponse)
async def progress(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    mastery = await topic_mastery(pool, user_id)
    retention = await service.get_retention_by_topic(pool, user_id)
    timeline = await service.get_timeline(pool, user_id)
    activity = await activity_last_7_days(pool, user_id)
    activity_max = max((day["attempt_count"] for day in activity), default=0)
    recommendation = service.get_recommendation(mastery)

    return templates.TemplateResponse(
        request,
        "analytics/progress.html",
        {
            "mastery": mastery,
            "retention": retention,
            "timeline": timeline,
            "activity": activity,
            "activity_max": activity_max,
            "recommendation": recommendation,
        },
    )
