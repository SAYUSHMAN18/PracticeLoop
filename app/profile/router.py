from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.service import set_role
from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.links import clean_url
from app.core.templates import templates
from app.core.usertime import clamp_rollover_hour
from app.profile import service

router = APIRouter(dependencies=[Depends(inject_current_user)])

MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10MB
GOAL_TYPE_LABELS = service.GOAL_TYPE_LABELS
PROFICIENCY_LABELS = service.PROFICIENCY_LABELS


def _parse_goal_type(goal_type: str) -> str:
    return goal_type if goal_type in GOAL_TYPE_LABELS else ""


def _parse_proficiency(proficiency_level: str) -> str:
    return proficiency_level if proficiency_level in PROFICIENCY_LABELS else ""


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
        {
            "profile": profile,
            "saved": False,
            "error": None,
            "goal_type_labels": GOAL_TYPE_LABELS,
            "proficiency_labels": PROFICIENCY_LABELS,
        },
    )


@router.post("/profile/role")
async def change_role(
    role: str = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    """Self-declared -- see auth/service.py's set_role for why that's
    fine at this app's scale. "teacher" only unlocks /classrooms's
    create-a-classroom form; it never grants access to anyone else's
    data by itself."""
    try:
        await set_role(pool, user_id, role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Not a valid role.") from exc
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile")
async def save_profile(
    request: Request,
    target_role: str = Form(""),
    target_companies: str = Form(""),
    goal_type: str = Form(""),
    target_date: str = Form(""),
    daily_time_budget_minutes: str = Form(""),
    timezone: str = Form(""),
    day_rollover_hour: str = Form("0"),
    proficiency_level: str = Form(""),
    review_reminders: bool = Form(False),
    github_url: str = Form(""),
    linkedin_url: str = Form(""),
    website_url: str = Form(""),
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
            service.validate_upload_content(resume.filename, content)
            resume_text = service.extract_text_from_file(resume.filename, content)
        except Exception:
            profile = await service.get_profile(pool, user_id)
            return templates.TemplateResponse(
                request,
                "profile/edit.html",
                {
                    "profile": profile,
                    "saved": False,
                    "error": "Couldn't read that file -- is it a valid PDF, DOCX, or text file?",
                    "goal_type_labels": GOAL_TYPE_LABELS,
                    "proficiency_labels": PROFICIENCY_LABELS,
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
        day_rollover_hour=clamp_rollover_hour(day_rollover_hour),
        proficiency_level=_parse_proficiency(proficiency_level),
    )
    # Checkbox is "send me reminders" (checked = on); the column stores the
    # opposite, so an absent checkbox means opted out.
    await service.set_digest_opt_out(pool, user_id, not review_reminders)
    await service.set_profile_links(
        pool,
        user_id,
        github=clean_url(github_url, host_contains="github.com"),
        linkedin=clean_url(linkedin_url, host_contains="linkedin.com"),
        website=clean_url(website_url),
    )

    profile = await service.get_profile(pool, user_id)
    return templates.TemplateResponse(
        request,
        "profile/edit.html",
        {
            "profile": profile,
            "saved": True,
            "error": None,
            "goal_type_labels": GOAL_TYPE_LABELS,
            "proficiency_labels": PROFICIENCY_LABELS,
        },
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
    in under three minutes" completion criteria, not toward it.

    Someone who's already onboarded and navigates back here (a stale tab,
    the browser back button) gets the dashboard, not the setup screen
    again."""
    if not await service.needs_onboarding(pool, user_id):
        return RedirectResponse("/dashboard", status_code=303)
    profile = await service.get_profile(pool, user_id)
    return templates.TemplateResponse(
        request,
        "profile/welcome.html",
        {"profile": profile, "goal_type_labels": GOAL_TYPE_LABELS, "proficiency_labels": PROFICIENCY_LABELS},
    )


@router.post("/welcome")
async def save_welcome(
    target_role: str = Form(""),
    goal_type: str = Form(""),
    target_date: str = Form(""),
    daily_time_budget_minutes: str = Form(""),
    proficiency_level: str = Form(""),
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
        proficiency_level=_parse_proficiency(proficiency_level),
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
