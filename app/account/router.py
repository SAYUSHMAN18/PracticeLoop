from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.account import service
from app.auth.service import AccountLocked, InvalidCredentials, authenticate
from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.security import log_out
from app.core.templates import templates

router = APIRouter(prefix="/account", dependencies=[Depends(inject_current_user)])


@router.get("", response_class=HTMLResponse)
async def account_page(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(request, "account/index.html", {"error": None})


@router.get("/export")
async def export_data(user_id: int = Depends(require_user_id), pool=Depends(get_pool)):
    data = await service.export_data(pool, user_id)
    body = json.dumps(data, default=str, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="practiceloop-data-export.json"'},
    )


@router.post("/delete")
async def delete_account(
    request: Request,
    password: str = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    cu = request.state.current_user
    try:
        await authenticate(pool, cu["email"], password)
    except (InvalidCredentials, AccountLocked):
        # AccountLocked can only happen if this same form was fumbled many
        # times -- treat it the same as a wrong password here (the account
        # is still theirs, still not deleted, and the lock clears itself).
        return templates.TemplateResponse(
            request,
            "account/index.html",
            {"error": "That password isn't correct -- your account was not deleted."},
            status_code=400,
        )

    await service.delete_account(pool, user_id)
    log_out(request)
    return RedirectResponse("/login?deleted=1", status_code=303)
