from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates
from app.profile import service

router = APIRouter(dependencies=[Depends(inject_current_user)])

MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10MB
GOAL_TYPE_LABELS = service.GOAL_TYPE_LABELS


def _parse_goal_type(goal_type: str) -> str:
    return goal_type if goal_type in GOAL_TYPE_LABELS else ""


def _parse_target_date(target_date: str) -> date | None:
    if not target_date.strip():
        return None
    try:
        return date.fromisoformat(target_date)
    except ValueError:
        return None


def _parse_time_budget(daily_time_budget_minutes: str) -> int | None:
    if not daily_time_budget_minutes.strip():
        return None
    try:
        return max(0, int(daily_time_budget_minutes))
    except ValueError:
        return None


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    profile = await service.get_profile(pool, user_id)
    return templates.TemplateResponse(
        request,
        "profile/edit.html",
        {"profile": profile, "saved": False, "error": None, "goal_type_labels": GOAL_TYPE_LABELS},
    )


@router.post("/profile")
async def save_profile(
    request: Request,
    target_role: str = Form(""),
    target_companies: str = Form(""),
    goal_type: str = Form(""),
    target_date: str = Form(""),
    daily_time_budget_minutes: str = Form(""),
    timezone: str = Form(""),
    resume: UploadFile | None = File(None),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    resume_text = None
    if resume is not None and resume.filename:
        # Read one byte past the cap so an oversized upload is detected
        # without ever holding the whole (possibly huge) file in memory.
        content = await resume.read(MAX_RESUME_BYTES + 1)
        if len(content) > MAX_RESUME_BYTES:
            raise HTTPException(status_code=413, detail="Resume file is too large (max 10MB).")
        try:
            resume_text = service.extract_resume_text(resume.filename, content)
        except Exception:
            profile = await service.get_profile(pool, user_id)
            return templates.TemplateResponse(
                request,
                "profile/edit.html",
                {
                    "profile": profile,
                    "saved": False,
                    "error": "Couldn't read that file -- is it a valid PDF or text file?",
                    "goal_type_labels": GOAL_TYPE_LABELS,
                },
                status_code=400,
            )

    await service.update_profile(
        pool,
        user_id,
        target_role,
        target_companies,
        resume_text,
        goal_type=_parse_goal_type(goal_type),
        target_date=_parse_target_date(target_date),
        daily_time_budget_minutes=_parse_time_budget(daily_time_budget_minutes),
        timezone=timezone,
    )

    profile = await service.get_profile(pool, user_id)
    return templates.TemplateResponse(
        request,
        "profile/edit.html",
        {"profile": profile, "saved": True, "error": None, "goal_type_labels": GOAL_TYPE_LABELS},
    )


@router.get("/welcome", response_class=HTMLResponse)
async def welcome_page(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    """One-time goal-setting screen shown right after signup/login until
    the user saves it or explicitly skips -- see profile/service.py's
    needs_onboarding/mark_onboarded. Deliberately just one screen, not the
    enhancement plan's full multi-step wizard: the existing signup form is
    built to take under a minute, and stacking a long wizard behind it
    would work against that same plan's own "personalized first activity
    in under three minutes" completion criteria, not toward it."""
    profile = await service.get_profile(pool, user_id)
    return templates.TemplateResponse(
        request, "profile/welcome.html", {"profile": profile, "goal_type_labels": GOAL_TYPE_LABELS}
    )


@router.post("/welcome")
async def save_welcome(
    target_role: str = Form(""),
    goal_type: str = Form(""),
    target_date: str = Form(""),
    daily_time_budget_minutes: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    profile = await service.get_profile(pool, user_id)
    await service.update_profile(
        pool,
        user_id,
        target_role,
        profile["target_companies"],
        goal_type=_parse_goal_type(goal_type),
        target_date=_parse_target_date(target_date),
        daily_time_budget_minutes=_parse_time_budget(daily_time_budget_minutes),
        timezone=profile["timezone"],
    )
    await service.mark_onboarded(pool, user_id)
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/welcome/skip")
async def skip_welcome(
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    await service.mark_onboarded(pool, user_id)
    return RedirectResponse("/dashboard", status_code=303)
