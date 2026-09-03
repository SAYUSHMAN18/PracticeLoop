from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates
from app.core.usertime import now_for, today_for
from app.dashboard import service
from app.documents.service import list_documents
from app.gamification.service import get_badges, grant_streak_shield, record_newly_earned
from app.jobs.applications import funnel_stats
from app.jobs.interview_prep import upcoming_interviews
from app.profile.service import GOAL_TYPE_LABELS

router = APIRouter(dependencies=[Depends(inject_current_user)])


def _time_of_day_greeting(tz_name: str) -> str:
    """now_for() already degrades an invalid or unset timezone to UTC -- a
    wrong greeting is cosmetic, not worth failing the whole dashboard."""
    hour = now_for(tz_name).hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    # Streak isn't in this batch: inject_current_user (a router-level
    # dependency, so it's already run) computed it once for the topbar,
    # and both the dashboard and get_badges below read that instead of
    # querying it again.
    streak = request.state.current_streak

    # The profile carries the timezone and day-rollover the rest of this
    # page's "today" depends on -- fetched first (a single indexed lookup)
    # so `today` is known before the concurrent batch that uses it. Every
    # count below is then anchored on the user's local day, matching what
    # the review queue itself hands them.
    profile = await pool.fetchrow(
        """SELECT target_role, resume_text, goal_type, target_date, timezone,
                  day_rollover_hour, streak_shields
           FROM profiles WHERE user_id = $1""",
        user_id,
    )
    tz_name = profile["timezone"] or ""
    today = today_for(tz_name, profile["day_rollover_hour"] or 0)

    # Eight independent reads -- none depends on another's result, so they're
    # fired concurrently instead of one at a time. Over a real network hop to
    # the database (as on Render, app and DB are separate services) that's
    # the difference between one round-trip's worth of latency and eight.
    (
        stats,
        mastery,
        interview_rows,
        jobs_funnel,
        documents,
        activity,
        new_concept,
        badges,
    ) = await asyncio.gather(
        service.get_stats(pool, user_id, today),
        service.topic_mastery(pool, user_id),
        upcoming_interviews(pool, user_id),
        funnel_stats(pool, user_id),
        list_documents(pool, user_id),
        service.activity_last_7_days(pool, user_id, today, tz_name),
        service.new_concept_recommendation(pool, user_id),
        get_badges(pool, user_id, streak_days=streak or 0),
    )
    # First time a badge's threshold is crossed, record it and drop a
    # notification -- the badge grid on this page is where they'll see it,
    # and the bell picks it up on the next navigation. A week-long streak
    # also earns a freeze here, at most one a week.
    await asyncio.gather(
        record_newly_earned(pool, user_id, badges),
        grant_streak_shield(pool, user_id, streak or 0),
    )

    interviews = [
        {"application": row, "days_until": (row["interview_at"] - datetime.now(timezone.utc)).days}
        for row in interview_rows
    ]
    document_count = len(documents)

    goal_days_until = None
    if profile["target_date"] is not None:
        goal_days_until = (profile["target_date"] - today).days
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
            "badges": badges,
            "streak_shields": profile["streak_shields"] or 0,
        },
    )
