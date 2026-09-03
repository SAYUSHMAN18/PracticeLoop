"""The digest cron endpoint and the one-click unsubscribe.

POST /cron/digest is bearer-token gated exactly like /jobs/cron/discover
-- fail closed if DIGEST_CRON_TOKEN is unset, non-2xx on a bad run so a
scheduled workflow actually notices. GET /digest/unsubscribe needs no
login: the signed token in the link is the authorization.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.config import settings
from app.core.db import get_pool
from app.core.templates import templates
from app.digest import service

router = APIRouter()


@router.post("/cron/digest")
async def cron_digest(request: Request, pool=Depends(get_pool)):
    configured = settings.digest_cron_token.strip()
    if not configured:
        raise HTTPException(status_code=503, detail="The digest job isn't configured on this instance.")

    submitted = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(submitted, configured):
        raise HTTPException(status_code=401, detail="Invalid or missing cron token.")

    result = await service.run_digest(pool)
    status_code = 500 if result["failed"] and not result["sent"] else 200
    return JSONResponse(result, status_code=status_code)


@router.get("/digest/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(request: Request, token: str = "", pool=Depends(get_pool)):
    user_id = service.user_id_from_unsubscribe_token(token) if token else None
    if user_id is not None:
        await pool.execute("UPDATE profiles SET digest_opt_out = true WHERE user_id = $1", user_id)
    # Same page either way -- a tampered token shouldn't get a different
    # response that confirms whether the id existed.
    return templates.TemplateResponse(request, "digest/unsubscribed.html", {})
