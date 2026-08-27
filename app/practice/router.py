from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.db import get_pool
from app.core.deps import require_user_id
from app.core.llm import is_configured as llm_is_configured
from app.core.llm_budget import require_llm_budget
from app.core.logging import get_logger
from app.core.templates import templates
from app.practice import extraction, grading, service

router = APIRouter(prefix="/practice")
logger = get_logger(__name__)


def _can_grade(card) -> bool:
    """Honest grading needs both an LLM to grade with and something to
    grade against -- a question saved with no answer (blank on capture,
    or a marker-parsed paste that never had one) falls back to self-rating
    even with an LLM configured, since there'd be nothing to compare the
    typed answer to."""
    return card is not None and bool(card["answer"]) and llm_is_configured()


def _fields_from_form(
    question: str,
    answer: str,
    example: str,
    topic: str,
    difficulty: str,
    company: str,
    code_snippet: str,
    language: str,
) -> dict:
    return {
        "question": question,
        "answer": answer,
        "example": example,
        "topic": topic,
        "difficulty": difficulty,
        "company": company,
        "code_snippet": code_snippet,
        "language": language,
    }


@router.get("", response_class=HTMLResponse)
async def practice_home(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    questions = await service.list_questions(pool, user_id)
    return templates.TemplateResponse(
        request,
        "practice/capture.html",
        {"questions": questions, "draft": None, "raw_text": "", "ai_error": None},
    )


@router.post("/structure", response_class=HTMLResponse)
async def structure_raw_text(
    request: Request,
    raw_text: str = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
    _budget: None = Depends(require_llm_budget),
):
    ai_error = None
    try:
        fields = await extraction.structure_with_llm(raw_text)
    except Exception as exc:
        logger.warning("AI structuring failed, falling back to marker parsing: %s", exc)
        ai_error = str(exc)
        fields = extraction.parse_markers(raw_text)

    questions = await service.list_questions(pool, user_id)
    return templates.TemplateResponse(
        request,
        "practice/capture.html",
        {"questions": questions, "draft": fields, "raw_text": raw_text, "ai_error": ai_error},
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
    fields = _fields_from_form(question, answer, example, topic, difficulty, company, code_snippet, language)
    await service.create_question(pool, user_id, fields, source="manual")
    return RedirectResponse("/practice", status_code=303)


@router.get("/{question_id}/edit", response_class=HTMLResponse)
async def edit_question_form(
    request: Request,
    question_id: int,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    question = await service.get_question(pool, user_id, question_id)
    if question is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "practice/edit.html", {"q": question, "saved": False})


@router.post("/{question_id}/edit")
async def edit_question(
    request: Request,
    question_id: int,
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
    fields = _fields_from_form(question, answer, example, topic, difficulty, company, code_snippet, language)
    try:
        await service.update_question(pool, user_id, question_id, fields)
    except service.QuestionNotFound as exc:
        raise HTTPException(status_code=404) from exc

    updated = await service.get_question(pool, user_id, question_id)
    return templates.TemplateResponse(request, "practice/edit.html", {"q": updated, "saved": True})


@router.post("/{question_id}/delete")
async def delete_question(
    question_id: int,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        await service.delete_question(pool, user_id, question_id)
    except service.QuestionNotFound as exc:
        raise HTTPException(status_code=404) from exc
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


@router.post("/study-card", response_class=HTMLResponse)
async def study_card(
    request: Request,
    topic: str = Form(...),
    difficulty: str = Form("medium"),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
    _budget: None = Depends(require_llm_budget),
):
    try:
        await service.generate_study_card(pool, user_id, topic, difficulty)
    except Exception as exc:
        logger.warning("Study-card generation failed: %s", exc)
        questions = await service.list_questions(pool, user_id)
        return templates.TemplateResponse(
            request,
            "practice/capture.html",
            {
                "questions": questions,
                "draft": None,
                "raw_text": "",
                "ai_error": None,
                "study_card_error": f"Couldn't generate a study card right now: {exc}",
            },
            status_code=502,
        )
    return RedirectResponse("/practice", status_code=303)


@router.get("/review", response_class=HTMLResponse)
async def review_queue(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    due = await service.due_for_review(pool, user_id)
    card = due[0] if due else None
    streak = await service.streak_days(pool, user_id)
    return templates.TemplateResponse(
        request,
        "practice/review.html",
        {"card": card, "remaining": len(due), "streak": streak, "can_grade": _can_grade(card)},
    )


@router.get("/review/next", response_class=HTMLResponse)
async def review_next_card(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    due = await service.due_for_review(pool, user_id)
    card = due[0] if due else None
    streak = await service.streak_days(pool, user_id)
    return templates.TemplateResponse(
        request,
        "practice/_review_card.html",
        {"card": card, "remaining": len(due), "streak": streak, "can_grade": _can_grade(card)},
    )


@router.post("/review/{question_id}", response_class=HTMLResponse)
async def rate_attempt(
    request: Request,
    question_id: int,
    rating: int = Form(..., ge=1, le=5),
    feedback: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    try:
        review_date = await service.record_attempt(pool, user_id, question_id, rating, feedback)
    except service.QuestionNotFound as exc:
        raise HTTPException(status_code=404) from exc

    days_until = (review_date - date.today()).days
    return templates.TemplateResponse(
        request,
        "practice/_review_result.html",
        {"review_date": review_date, "days_until": days_until},
    )


@router.post("/review/{question_id}/grade", response_class=HTMLResponse)
async def grade_review_answer(
    request: Request,
    question_id: int,
    answer: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
    _budget: None = Depends(require_llm_budget),
):
    question = await service.get_question(pool, user_id, question_id)
    if question is None:
        raise HTTPException(status_code=404)

    try:
        result = await grading.grade_answer(question["question"], question["answer"], answer)
    except Exception as exc:
        # A transient grading failure shouldn't break the app's single
        # most-used flow -- fall back to the plain self-rate card for this
        # one question rather than a 500 or a lost typed answer.
        logger.warning("Grading failed, falling back to self-rate: %s", exc)
        due = await service.due_for_review(pool, user_id)
        streak = await service.streak_days(pool, user_id)
        return templates.TemplateResponse(
            request,
            "practice/_review_card.html",
            {"card": question, "remaining": len(due), "streak": streak, "can_grade": False},
        )

    review_date = await service.record_attempt(
        pool, user_id, question_id, result["rating"], result["feedback"]
    )
    days_until = (review_date - date.today()).days
    return templates.TemplateResponse(
        request,
        "practice/_review_graded_result.html",
        {
            "rating": result["rating"],
            "feedback": result["feedback"],
            "correct_answer": question["answer"],
            "review_date": review_date,
            "days_until": days_until,
        },
    )
