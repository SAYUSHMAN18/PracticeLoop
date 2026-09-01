from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.llm import is_configured as llm_is_configured
from app.core.templates import templates
from app.labs import math_service, writing_service

router = APIRouter(prefix="/labs", dependencies=[Depends(inject_current_user)])


@router.get("", response_class=HTMLResponse)
async def labs_hub(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(request, "labs/index.html", {})


@router.get("/math", response_class=HTMLResponse)
async def math_lab(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(
        request, "labs/math.html", {"equation": "", "result": None, "steps": None, "error": None}
    )


@router.post("/math/solve", response_class=HTMLResponse)
async def math_lab_solve(
    request: Request,
    equation: str = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    result = None
    steps = None
    error = None
    try:
        result = math_service.solve_equation(equation)
    except math_service.InvalidEquation as exc:
        error = str(exc)

    if result is not None:
        steps = await math_service.generate_steps(
            pool,
            user_id,
            result["equation_display"],
            result["solutions"],
            ai_available=llm_is_configured(),
        )

    return templates.TemplateResponse(
        request,
        "labs/math.html",
        {"equation": equation, "result": result, "steps": steps, "error": error},
    )


@router.get("/writing", response_class=HTMLResponse)
async def writing_lab(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(
        request,
        "labs/writing.html",
        {"text": "", "kind": "essay", "kinds": writing_service.KINDS, "feedback": None, "error": None},
    )


@router.post("/writing/review", response_class=HTMLResponse)
async def writing_lab_review(
    request: Request,
    text: str = Form(...),
    kind: str = Form("essay"),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    feedback = None
    error = None
    try:
        feedback = await writing_service.get_feedback(text, kind, ai_available=llm_is_configured())
    except writing_service.WritingFeedbackUnavailable:
        error = "Writing Lab needs an AI provider configured -- ask your admin to set one up."
    except writing_service.WritingFeedbackFailed as exc:
        error = str(exc)

    return templates.TemplateResponse(
        request,
        "labs/writing.html",
        {
            "text": text,
            "kind": kind,
            "kinds": writing_service.KINDS,
            "feedback": feedback,
            "error": error,
        },
    )
