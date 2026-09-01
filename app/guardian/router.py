from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates
from app.guardian import service

router = APIRouter(prefix="/guardian", dependencies=[Depends(inject_current_user)])


@router.get("", response_class=HTMLResponse)
async def guardian_index(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    my_invites = await service.list_invites_for_student(pool, user_id)
    my_students = await service.list_students_for_guardian(pool, user_id)
    return templates.TemplateResponse(
        request,
        "guardian/index.html",
        {"invites": my_invites, "students": my_students},
    )


@router.post("/invite")
async def create_invite(user_id: int = Depends(require_user_id), pool=Depends(get_pool)):
    await service.create_invite(pool, user_id)
    return RedirectResponse("/guardian", status_code=303)


@router.post("/invite/{link_id}/revoke")
async def revoke_invite(link_id: int, user_id: int = Depends(require_user_id), pool=Depends(get_pool)):
    try:
        await service.revoke_invite(pool, user_id, link_id)
    except service.InviteNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return RedirectResponse("/guardian", status_code=303)


@router.get("/accept/{invite_token}", response_class=HTMLResponse)
async def accept_invite_preview(
    invite_token: str,
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        preview = await service.get_invite_preview(pool, invite_token)
    except service.InviteNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return templates.TemplateResponse(
        request, "guardian/accept.html", {"preview": preview, "invite_token": invite_token}
    )


@router.post("/accept/{invite_token}")
async def accept_invite(
    invite_token: str,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        await service.accept_invite(pool, user_id, invite_token)
    except service.InviteNotFound as exc:
        raise HTTPException(status_code=404) from exc
    except service.CannotGuardSelf as exc:
        raise HTTPException(status_code=400, detail="You can't accept your own guardian invite.") from exc
    return RedirectResponse("/guardian", status_code=303)
