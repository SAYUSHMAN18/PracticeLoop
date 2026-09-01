from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.llm import is_configured as llm_is_configured
from app.core.templates import templates
from app.mentor import service

router = APIRouter(prefix="/mentor", dependencies=[Depends(inject_current_user)])


def _clean_context(context_type: str, context_id: int | None) -> tuple[str, int | None]:
    if context_type not in service.VALID_CONTEXT_TYPES:
        return "general", None
    if context_type == "general":
        return "general", None
    return context_type, context_id


async def _render_conversation(
    request: Request, pool, user_id: int, context_type: str, context_id: int | None
):
    context_type, context_id = _clean_context(context_type, context_id)
    conversation_id = await service.get_or_create_conversation(pool, user_id, context_type, context_id)
    messages = await service.list_messages(pool, user_id, conversation_id)
    # A canned ("no AI provider configured", budget-exhausted, generation-
    # failed) reply isn't actually AI-generated -- labeling it that way
    # would itself be a small dishonesty, so it skips the disclaimer.
    for message in messages:
        message["is_ai_generated"] = (
            message["role"] == "assistant" and message["content"] not in service.CANNED_REPLIES
        )
    return templates.TemplateResponse(
        request,
        "mentor/_conversation.html",
        {
            "messages": messages,
            "context_type": context_type,
            "context_id": context_id or "",
            "quick_actions": service.QUICK_ACTIONS,
            "ai_available": llm_is_configured(),
        },
    )


@router.get("/conversation", response_class=HTMLResponse)
async def conversation(
    request: Request,
    context_type: str = "general",
    context_id: str = "",
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    parsed_id = int(context_id) if context_id.isdigit() else None
    return await _render_conversation(request, pool, user_id, context_type, parsed_id)


@router.post("/message", response_class=HTMLResponse)
async def send_message(
    request: Request,
    text: str = Form(...),
    context_type: str = Form("general"),
    context_id: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    text = text.strip()
    parsed_id = int(context_id) if context_id.isdigit() else None
    clean_type, clean_id = _clean_context(context_type, parsed_id)

    if text:
        conversation_id = await service.get_or_create_conversation(pool, user_id, clean_type, clean_id)
        context = await service.build_context(pool, user_id, clean_type, clean_id)
        try:
            await service.send_message(
                pool, user_id, conversation_id, text, context=context, ai_available=llm_is_configured()
            )
        except service.ConversationNotFound as exc:
            raise HTTPException(status_code=404) from exc

    return await _render_conversation(request, pool, user_id, clean_type, clean_id)


@router.post("/quick-action", response_class=HTMLResponse)
async def quick_action(
    request: Request,
    action_id: str = Form(...),
    context_type: str = Form("general"),
    context_id: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    text = service.QUICK_ACTIONS.get(action_id)
    if text is None:
        raise HTTPException(status_code=404)

    parsed_id = int(context_id) if context_id.isdigit() else None
    clean_type, clean_id = _clean_context(context_type, parsed_id)
    conversation_id = await service.get_or_create_conversation(pool, user_id, clean_type, clean_id)
    context = await service.build_context(pool, user_id, clean_type, clean_id)
    await service.send_message(
        pool, user_id, conversation_id, text, context=context, ai_available=llm_is_configured()
    )
    return await _render_conversation(request, pool, user_id, clean_type, clean_id)
