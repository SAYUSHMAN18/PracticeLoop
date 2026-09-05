from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates
from app.notifications import push, service

router = APIRouter(prefix="/notifications", dependencies=[Depends(inject_current_user)])


@router.get("/panel", response_class=HTMLResponse)
async def panel(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    notifications = await service.list_notifications(pool, user_id)
    return templates.TemplateResponse(request, "notifications/_panel.html", {"notifications": notifications})


@router.post("/{notification_id}/read")
async def mark_read(notification_id: int, user_id: int = Depends(require_user_id), pool=Depends(get_pool)):
    try:
        notification = await service.mark_read(pool, user_id, notification_id)
    except service.NotificationNotFound as exc:
        raise HTTPException(status_code=404) from exc
    # Marking one read is how a student actually opens it -- go to
    # whatever it's about, not just back to the dashboard.
    destination = notification["link"] or "/dashboard"
    return RedirectResponse(destination, status_code=303)


@router.post("/read-all", response_class=HTMLResponse)
async def mark_all_read(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    await service.mark_all_read(pool, user_id)
    notifications = await service.list_notifications(pool, user_id)
    return templates.TemplateResponse(request, "notifications/_panel.html", {"notifications": notifications})


# --- web push -------------------------------------------------------------


@router.post("/push/subscribe")
async def push_subscribe(request: Request, user_id: int = Depends(require_user_id), pool=Depends(get_pool)):
    """Body is a browser PushSubscription's own .toJSON() shape --
    {endpoint, keys: {p256dh, auth}} -- passed straight through rather
    than reshaped into form fields, since that's the exact object
    push-subscribe.js already has in hand from the Push API."""
    try:
        payload = await request.json()
        endpoint = str(payload["endpoint"])
        p256dh = str(payload["keys"]["p256dh"])
        auth = str(payload["keys"]["auth"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Malformed push subscription.") from exc

    await push.add_subscription(pool, user_id, endpoint=endpoint, p256dh=p256dh, auth=auth)
    return JSONResponse({"ok": True})


@router.post("/push/unsubscribe")
async def push_unsubscribe(request: Request, user_id: int = Depends(require_user_id), pool=Depends(get_pool)):
    payload = await request.json()
    endpoint = str(payload.get("endpoint", ""))
    if endpoint:
        await push.remove_subscription(pool, user_id, endpoint)
    return JSONResponse({"ok": True})
