from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.templates import templates
from app.decks import service
from app.practice.service import list_topics

router = APIRouter(prefix="/decks", dependencies=[Depends(inject_current_user)])


async def _render_index(request: Request, pool, user_id: int, *, error: str | None, q: str = ""):
    public_decks, my_decks, topics = await asyncio.gather(
        service.list_public_decks(pool, query=q),
        service.list_my_decks(pool, user_id),
        list_topics(pool, user_id),
    )
    return templates.TemplateResponse(
        request,
        "decks/index.html",
        {"public_decks": public_decks, "my_decks": my_decks, "topics": topics, "q": q, "error": error},
    )


@router.get("", response_class=HTMLResponse)
async def decks_index(
    request: Request,
    q: str = "",
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    return await _render_index(request, pool, user_id, error=None, q=q)


@router.post("")
async def publish_deck(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    topic: str = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        deck_id = await service.publish_deck(pool, user_id, name=name, description=description, topic=topic)
    except service.NameRejected as exc:
        return await _render_index(request, pool, user_id, error=str(exc))
    except service.EmptyTopic:
        return await _render_index(
            request, pool, user_id, error="You have no questions tagged with that topic yet."
        )
    return RedirectResponse(f"/decks/{deck_id}", status_code=303)


@router.get("/{deck_id}", response_class=HTMLResponse)
async def deck_detail(
    deck_id: int,
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        deck = await service.get_deck_detail(pool, deck_id)
    except service.DeckNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return templates.TemplateResponse(
        request,
        "decks/detail.html",
        {"deck": deck, "is_owner": deck["owner_user_id"] == user_id, "result": None},
    )


@router.post("/{deck_id}/import", response_class=HTMLResponse)
async def import_deck(
    deck_id: int,
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        result = await service.import_deck(pool, user_id, deck_id)
        deck = await service.get_deck_detail(pool, deck_id)
    except service.DeckNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return templates.TemplateResponse(
        request,
        "decks/detail.html",
        {"deck": deck, "is_owner": deck["owner_user_id"] == user_id, "result": result},
    )


@router.post("/{deck_id}/delete")
async def delete_deck(deck_id: int, user_id: int = Depends(require_user_id), pool=Depends(get_pool)):
    try:
        await service.delete_deck(pool, user_id, deck_id)
    except service.DeckNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return RedirectResponse("/decks", status_code=303)
