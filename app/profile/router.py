from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.db import get_pool
from app.core.deps import require_user_id
from app.profile import service

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    profile = await service.get_profile(pool, user_id)
    return templates.TemplateResponse(
        request, "profile/edit.html", {"profile": profile, "saved": False}
    )


@router.post("/profile")
async def save_profile(
    request: Request,
    target_role: str = Form(""),
    target_companies: str = Form(""),
    resume: UploadFile | None = File(None),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    resume_text = None
    if resume is not None and resume.filename:
        content = await resume.read()
        resume_text = service.extract_resume_text(resume.filename, content)

    await service.update_profile(pool, user_id, target_role, target_companies, resume_text)

    profile = await service.get_profile(pool, user_id)
    return templates.TemplateResponse(
        request, "profile/edit.html", {"profile": profile, "saved": True}
    )
