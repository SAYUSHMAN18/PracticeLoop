from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates
from app.profile import service

router = APIRouter(dependencies=[Depends(inject_current_user)])

MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10MB
GOAL_TYPE_LABELS = service.GOAL_TYPE_LABELS


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
    if goal_type not in GOAL_TYPE_LABELS:
        goal_type = ""

    parsed_target_date = None
    if target_date.strip():
        try:
            parsed_target_date = date.fromisoformat(target_date)
        except ValueError:
            parsed_target_date = None

    parsed_budget = None
    if daily_time_budget_minutes.strip():
        try:
            parsed_budget = max(0, int(daily_time_budget_minutes))
        except ValueError:
            parsed_budget = None

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
        goal_type=goal_type,
        target_date=parsed_target_date,
        daily_time_budget_minutes=parsed_budget,
        timezone=timezone,
    )

    profile = await service.get_profile(pool, user_id)
    return templates.TemplateResponse(
        request,
        "profile/edit.html",
        {"profile": profile, "saved": True, "error": None, "goal_type_labels": GOAL_TYPE_LABELS},
    )
