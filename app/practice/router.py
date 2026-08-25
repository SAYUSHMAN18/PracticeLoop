from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.db import get_pool
from app.core.deps import require_user_id
from app.practice import extraction, service

router = APIRouter(prefix="/practice")
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def practice_home(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    questions = await service.list_questions(pool, user_id)
    return templates.TemplateResponse(
        request, "practice/capture.html", {"questions": questions, "draft": None, "raw_text": ""}
    )


@router.post("/structure", response_class=HTMLResponse)
async def structure_raw_text(
    request: Request,
    raw_text: str = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        fields = await extraction.structure_with_llm(raw_text)
    except Exception:
        fields = extraction.parse_markers(raw_text)

    questions = await service.list_questions(pool, user_id)
    return templates.TemplateResponse(
        request, "practice/capture.html", {"questions": questions, "draft": fields, "raw_text": raw_text}
    )


@router.post("")
async def save_question(
    question: str = Form(...),
    answer: str = Form(""),
    example: str = Form(""),
    topic: str = Form(""),
    difficulty: str = Form("medium"),
    company: str = Form(""),
    code_snippet: str = Form(""),
    language: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    fields = {
        "question": question,
        "answer": answer,
        "example": example,
        "topic": topic,
        "difficulty": difficulty,
        "company": company,
        "code_snippet": code_snippet,
        "language": language,
    }
    await service.create_question(pool, user_id, fields, source="manual")
    return RedirectResponse("/practice", status_code=303)


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = "",
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    results = await service.search_questions(pool, user_id, q, top_k=5) if q.strip() else []
    return templates.TemplateResponse(
        request, "practice/_search_results.html", {"results": results, "query": q}
    )


@router.post("/study-card")
async def study_card(
    topic: str = Form(...),
    difficulty: str = Form("medium"),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    await service.generate_study_card(pool, user_id, topic, difficulty)
    return RedirectResponse("/practice", status_code=303)


@router.get("/review", response_class=HTMLResponse)
async def review_queue(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    due = await service.due_for_review(pool, user_id)
    streak = await service.streak_days(pool, user_id)
    return templates.TemplateResponse(
        request, "practice/review.html", {"due": due, "streak": streak}
    )


@router.post("/review/{question_id}")
async def rate_attempt(
    question_id: int,
    rating: int = Form(...),
    feedback: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    await service.record_attempt(pool, user_id, question_id, rating, feedback)
    return RedirectResponse("/practice/review", status_code=303)
