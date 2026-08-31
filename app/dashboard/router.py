from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates
from app.dashboard import service
from app.documents.service import list_documents
from app.jobs.applications import funnel_stats
from app.jobs.interview_prep import upcoming_interviews
from app.practice.service import streak_days
from app.profile.service import GOAL_TYPE_LABELS

router = APIRouter(dependencies=[Depends(inject_current_user)])


def _time_of_day_greeting(tz_name: str) -> str:
    """Best-effort: an invalid or unset timezone (free text on the profile,
    never validated against the IANA database) falls back to UTC rather
    than guessing -- a wrong greeting is cosmetic, not worth failing the
    whole dashboard over."""
    try:
        now = datetime.now(ZoneInfo(tz_name)) if tz_name else datetime.now(timezone.utc)
    except (ZoneInfoNotFoundError, ValueError):
        now = datetime.now(timezone.utc)

    if now.hour < 12:
        return "Good morning"
    if now.hour < 18:
        return "Good afternoon"
    return "Good evening"


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    # Nine independent reads -- none depends on another's result, so they're
    # fired concurrently instead of one at a time. Over a real network hop to
    # the database (as on Render, app and DB are separate services) that's
    # the difference between one round-trip's worth of latency and nine.
    (
        stats,
        streak,
        mastery,
        interview_rows,
        jobs_funnel,
        profile,
        documents,
        activity,
        new_concept,
    ) = await asyncio.gather(
        service.get_stats(pool, user_id),
        streak_days(pool, user_id),
        service.topic_mastery(pool, user_id),
        upcoming_interviews(pool, user_id),
        funnel_stats(pool, user_id),
        pool.fetchrow(
            """SELECT target_role, resume_text, goal_type, target_date, timezone
               FROM profiles WHERE user_id = $1""",
            user_id,
        ),
        list_documents(pool, user_id),
        service.activity_last_7_days(pool, user_id),
        service.new_concept_recommendation(pool, user_id),
    )
    interviews = [
        {"application": row, "days_until": (row["interview_at"] - datetime.now(timezone.utc)).days}
        for row in interview_rows
    ]
    document_count = len(documents)

    goal_days_until = None
    if profile["target_date"] is not None:
        goal_days_until = (profile["target_date"] - date.today()).days
    goal_type_label = GOAL_TYPE_LABELS.get(profile["goal_type"], "").lower() or "your goal"

    cu = request.state.current_user
    first_name = (cu["name"].split()[0] if cu and cu["name"].split() else None) if cu else None
    greeting = _time_of_day_greeting(profile["timezone"])
    activity_max = max((day["attempt_count"] for day in activity), default=0)

    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "stats": stats,
            "streak": streak,
            "mastery": mastery,
            "interviews": interviews,
            "jobs_funnel": jobs_funnel,
            "profile": profile,
            "document_count": document_count,
            "goal_days_until": goal_days_until,
            "goal_type_label": goal_type_label,
            "has_goal_type": bool(profile["goal_type"]),
            "greeting": greeting,
            "first_name": first_name,
            "activity": activity,
            "activity_max": activity_max,
            "new_concept": new_concept,
        },
    )
