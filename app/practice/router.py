from __future__ import annotations

import asyncio
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.llm import is_configured as llm_is_configured
from app.core.llm_budget import require_llm_budget
from app.core.logging import get_logger
from app.core.templates import templates
from app.practice import extraction, grading, service

router = APIRouter(prefix="/practice", dependencies=[Depends(inject_current_user)])
logger = get_logger(__name__)

_PLAN_SESSION_KEY = "daily_plan"
_PLAN_REASON_LABELS = {"due": "Due", "weak": "Weak spot", "new": "New", "challenge": "Challenge"}


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


def _active_plan_ids(request: Request) -> list[int] | None:
    """None means "no plan started today, use the normal due queue." An
    empty list is a real, distinct state -- a plan that was started and
    has since been fully worked through -- so it must not be treated the
    same as "no plan," or a finished plan would silently fall back to
    showing every other due card instead of "all caught up.\""""
    plan = request.session.get(_PLAN_SESSION_KEY)
    if not plan or plan.get("date") != date.today().isoformat():
        return None
    return plan.get("question_ids", [])


def _start_plan(request: Request, question_ids: list[int]) -> None:
    request.session[_PLAN_SESSION_KEY] = {"date": date.today().isoformat(), "question_ids": question_ids}


def _remove_from_plan(request: Request, question_id: int) -> None:
    plan = request.session.get(_PLAN_SESSION_KEY)
    if plan and plan.get("date") == date.today().isoformat():
        plan["question_ids"] = [qid for qid in plan.get("question_ids", []) if qid != question_id]
        request.session[_PLAN_SESSION_KEY] = plan


async def _load_queue(request: Request, pool, user_id: int) -> list:
    """The session's active plan (if one was started today and isn't
    finished) replaces the normal due-cards query -- same shape either
    way, a plain list of question records in the order to work through."""
    plan_ids = _active_plan_ids(request)
    if plan_ids is not None:
        return await service.get_questions_by_ids(pool, user_id, plan_ids)
    return await service.due_for_review(pool, user_id)


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


@router.get("/plan", response_class=HTMLResponse)
async def plan_preview(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    """Phase 3.2's adaptive daily session, previewed before it's started --
    see service.build_daily_plan for what actually goes into it."""
    plan = await service.build_daily_plan(pool, user_id)
    counts: dict[str, int] = {}
    for item in plan:
        counts[item["reason"]] = counts.get(item["reason"], 0) + 1
    return templates.TemplateResponse(
        request,
        "practice/plan.html",
        {"plan": plan, "counts": counts, "reason_labels": _PLAN_REASON_LABELS},
    )


@router.post("/plan/start")
async def start_plan(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    plan = await service.build_daily_plan(pool, user_id)
    _start_plan(request, [item["question"]["question_id"] for item in plan])
    return RedirectResponse("/practice/review", status_code=303)


@router.get("/review", response_class=HTMLResponse)
async def review_queue(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    due, streak = await asyncio.gather(
        _load_queue(request, pool, user_id), service.streak_days(pool, user_id)
    )
    card = due[0] if due else None
    return templates.TemplateResponse(
        request,
        "practice/review.html",
        {
            "card": card,
            "remaining": len(due),
            "streak": streak,
            "can_grade": _can_grade(card),
            "in_plan": _active_plan_ids(request) is not None,
        },
    )


@router.get("/review/next", response_class=HTMLResponse)
async def review_next_card(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    due, streak = await asyncio.gather(
        _load_queue(request, pool, user_id), service.streak_days(pool, user_id)
    )
    card = due[0] if due else None
    return templates.TemplateResponse(
        request,
        "practice/_review_card.html",
        {
            "card": card,
            "remaining": len(due),
            "streak": streak,
            "can_grade": _can_grade(card),
            "in_plan": _active_plan_ids(request) is not None,
        },
    )


@router.post("/review/{question_id}/skip", response_class=HTMLResponse)
async def skip_plan_item(
    request: Request,
    question_id: int,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    """Phase 3.2's "replace an activity" control, in its simplest honest
    form: drop today's plan down to one item, no attempt recorded, no FSRS
    schedule touched. Only meaningful mid-plan -- outside of one, there's
    nothing session-side to remove a card from."""
    _remove_from_plan(request, question_id)
    due, streak = await asyncio.gather(
        _load_queue(request, pool, user_id), service.streak_days(pool, user_id)
    )
    card = due[0] if due else None
    return templates.TemplateResponse(
        request,
        "practice/_review_card.html",
        {
            "card": card,
            "remaining": len(due),
            "streak": streak,
            "can_grade": _can_grade(card),
            "in_plan": _active_plan_ids(request) is not None,
        },
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

    _remove_from_plan(request, question_id)
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
        # one question rather than a 500 or a lost typed answer. The
        # question stays in the plan (if any) since no attempt was
        # actually recorded yet.
        logger.warning("Grading failed, falling back to self-rate: %s", exc)
        due, streak = await asyncio.gather(
            _load_queue(request, pool, user_id), service.streak_days(pool, user_id)
        )
        return templates.TemplateResponse(
            request,
            "practice/_review_card.html",
            {
                "card": question,
                "remaining": len(due),
                "streak": streak,
                "can_grade": False,
                "in_plan": _active_plan_ids(request) is not None,
            },
        )

    review_date = await service.record_attempt(
        pool, user_id, question_id, result["rating"], result["feedback"]
    )
    _remove_from_plan(request, question_id)
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
