from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates
from app.profile import service

router = APIRouter(dependencies=[Depends(inject_current_user)])

MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10MB


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    profile = await service.get_profile(pool, user_id)
    return templates.TemplateResponse(
        request, "profile/edit.html", {"profile": profile, "saved": False, "error": None}
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
                },
                status_code=400,
            )

    await service.update_profile(pool, user_id, target_role, target_companies, resume_text)

    profile = await service.get_profile(pool, user_id)
    return templates.TemplateResponse(
        request, "profile/edit.html", {"profile": profile, "saved": True, "error": None}
    )
