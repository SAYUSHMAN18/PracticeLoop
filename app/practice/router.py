from __future__ import annotations

import asyncio
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.llm import is_configured as llm_is_configured
from app.core.llm_budget import require_llm_budget
from app.core.logging import get_logger
from app.core.markdown import render_markdown
from app.core.templates import templates
from app.practice import bulk_io, extraction, grading, service

router = APIRouter(prefix="/practice", dependencies=[Depends(inject_current_user)])
logger = get_logger(__name__)

_PLAN_SESSION_KEY = "daily_plan"
_PLAN_REASON_LABELS = {"due": "Due", "weak": "Weak spot", "new": "New", "challenge": "Challenge"}

# Phase 10's Quiz Arena: a replay-anything batch quiz over the whole
# question bank (not just what's due), separate from the FSRS review
# queue above -- same session-scoped-state pattern as the daily plan,
# since a quiz-in-progress isn't meant to be durable either.
_QUIZ_ARENA_SESSION_KEY = "quiz_arena"
_QUIZ_ARENA_COUNTS = (5, 10, 15, 20)


def _can_grade(card) -> bool:
    """Honest grading needs both an LLM to grade with and something to
    grade against -- a question saved with no answer (blank on capture,
    or a marker-parsed paste that never had one) falls back to self-rating
    even with an LLM configured, since there'd be nothing to compare the
    typed answer to. Multiple-choice never reaches this path at all --
    it's graded deterministically, not by the LLM grader -- but is
    excluded explicitly rather than relying on the template's branch
    order to make that true."""
    return (
        card is not None
        and card["question_type"] != "multiple_choice"
        and bool(card["answer"])
        and llm_is_configured()
    )


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


def _active_plan_ids(request: Request, today: date) -> list[int] | None:
    """None means "no plan started today, use the normal due queue." An
    empty list is a real, distinct state -- a plan that was started and
    has since been fully worked through -- so it must not be treated the
    same as "no plan," or a finished plan would silently fall back to
    showing every other due card instead of "all caught up."

    `today` is the user's local study day (see app.core.usertime), so a
    plan started late at night rolls over on the same boundary the review
    queue and streak use, not the server's UTC midnight."""
    plan = request.session.get(_PLAN_SESSION_KEY)
    if not plan or plan.get("date") != today.isoformat():
        return None
    return plan.get("question_ids", [])


def _start_plan(request: Request, question_ids: list[int], today: date) -> None:
    request.session[_PLAN_SESSION_KEY] = {"date": today.isoformat(), "question_ids": question_ids}


def _remove_from_plan(request: Request, question_id: int, today: date) -> None:
    plan = request.session.get(_PLAN_SESSION_KEY)
    if plan and plan.get("date") == today.isoformat():
        plan["question_ids"] = [qid for qid in plan.get("question_ids", []) if qid != question_id]
        request.session[_PLAN_SESSION_KEY] = plan


async def _user_today(request: Request, pool, user_id: int) -> date:
    """The user's local study day, computed once per request and cached on
    request.state -- several handlers here need it two or three times
    (queue load, plan staleness, "review in N days") and it's one indexed
    profile lookup."""
    cached = getattr(request.state, "_practice_today", None)
    if cached is None:
        cached = await service.user_today(pool, user_id)
        request.state._practice_today = cached
    return cached


async def _load_queue(request: Request, pool, user_id: int) -> list:
    """The session's active plan (if one was started today and isn't
    finished) replaces the normal due-cards query -- same shape either
    way, a plain list of question records in the order to work through."""
    plan_ids = _active_plan_ids(request, await _user_today(request, pool, user_id))
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


@router.get("/import", response_class=HTMLResponse)
async def import_form(request: Request, user_id: int = Depends(require_user_id)):
    return templates.TemplateResponse(
        request, "practice/import.html", {"result": None, "error": None, "pasted": ""}
    )


@router.post("/import", response_class=HTMLResponse)
async def import_questions(
    request: Request,
    pasted: str = Form(""),
    file: UploadFile | None = File(None),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    text = pasted
    if file is not None and file.filename:
        raw = await file.read(1_000_000)  # 1 MB is plenty for a text deck
        text = raw.decode("utf-8", errors="replace")

    rows = bulk_io.parse_bulk(text)
    if not rows:
        return templates.TemplateResponse(
            request,
            "practice/import.html",
            {
                "result": None,
                "error": "Found no rows to import -- each line needs a question and an answer, "
                "separated by a tab or a comma.",
                "pasted": pasted,
            },
            status_code=400,
        )

    existing = {q["question"].casefold() for q in await service.list_questions(pool, user_id)}
    added = 0
    for row in rows:
        if row["question"].casefold() in existing:
            continue
        await service.create_question(pool, user_id, row, source="imported")
        existing.add(row["question"].casefold())
        added += 1

    return templates.TemplateResponse(
        request,
        "practice/import.html",
        {"result": {"added": added, "skipped": len(rows) - added}, "error": None, "pasted": ""},
    )


@router.get("/export.csv")
async def export_csv(user_id: int = Depends(require_user_id), pool=Depends(get_pool)):
    rows = await service.list_questions(pool, user_id)
    return PlainTextResponse(
        bulk_io.to_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="practiceloop-questions.csv"'},
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
    today = await _user_today(request, pool, user_id)
    _start_plan(request, [item["question"]["question_id"] for item in plan], today)
    return RedirectResponse("/practice/review", status_code=303)


@router.get("/history", response_class=HTMLResponse)
async def study_history(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    """Phase 3.3, narrowed to a plain activity log -- see
    service.study_history for why the fuller calendar/reminders vision
    isn't built here."""
    attempts = await service.study_history(pool, user_id)

    days: list[dict] = []
    by_date: dict = {}
    for a in attempts:
        d = a["practiced_at"].date()
        if d not in by_date:
            by_date[d] = {"date": d, "attempts": []}
            days.append(by_date[d])
        by_date[d]["attempts"].append(a)

    return templates.TemplateResponse(request, "practice/history.html", {"days": days})


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
            "in_plan": _active_plan_ids(request, await _user_today(request, pool, user_id)) is not None,
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
            "in_plan": _active_plan_ids(request, await _user_today(request, pool, user_id)) is not None,
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
    today = await _user_today(request, pool, user_id)
    _remove_from_plan(request, question_id, today)
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
            "in_plan": _active_plan_ids(request, today) is not None,
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

    today = await _user_today(request, pool, user_id)
    _remove_from_plan(request, question_id, today)
    days_until = (review_date - today).days
    return templates.TemplateResponse(
        request,
        "practice/_review_result.html",
        {"review_date": review_date, "days_until": days_until},
    )


@router.post("/review/{question_id}/answer-choice", response_class=HTMLResponse)
async def answer_mcq(
    request: Request,
    question_id: int,
    selected_index: int = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    question = await service.get_question(pool, user_id, question_id)
    if question is None:
        raise HTTPException(status_code=404)

    try:
        review_date, is_correct = await service.record_mcq_attempt(pool, user_id, question_id, selected_index)
    except service.QuestionNotFound as exc:
        raise HTTPException(status_code=404) from exc

    today = await _user_today(request, pool, user_id)
    _remove_from_plan(request, question_id, today)
    days_until = (review_date - today).days
    correct_choice = question["choices"][question["correct_choice_index"]]
    return templates.TemplateResponse(
        request,
        "practice/_review_mcq_result.html",
        {
            "is_correct": is_correct,
            "correct_choice": correct_choice,
            "review_date": review_date,
            "days_until": days_until,
        },
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
                "in_plan": _active_plan_ids(request, await _user_today(request, pool, user_id)) is not None,
            },
        )

    today = await _user_today(request, pool, user_id)
    review_date = await service.record_attempt(
        pool, user_id, question_id, result["rating"], result["feedback"]
    )
    _remove_from_plan(request, question_id, today)
    days_until = (review_date - today).days
    return templates.TemplateResponse(
        request,
        "practice/_review_graded_result.html",
        {
            "rating": result["rating"],
            "feedback": render_markdown(result["feedback"]),
            "correct_answer": question["answer"],
            "review_date": review_date,
            "days_until": days_until,
        },
    )


@router.get("/quiz-arena", response_class=HTMLResponse)
async def quiz_arena_start(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    topics = await service.list_topics(pool, user_id)
    return templates.TemplateResponse(
        request,
        "practice/quiz_arena_start.html",
        {"topics": topics, "counts": _QUIZ_ARENA_COUNTS, "error": None},
    )


@router.post("/quiz-arena/start")
async def quiz_arena_begin(
    request: Request,
    topic: str = Form(""),
    count: int = Form(10),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    count = count if count in _QUIZ_ARENA_COUNTS else 10
    questions = await service.get_quiz_questions(pool, user_id, topic=topic or None, count=count)
    if not questions:
        topics = await service.list_topics(pool, user_id)
        return templates.TemplateResponse(
            request,
            "practice/quiz_arena_start.html",
            {
                "topics": topics,
                "counts": _QUIZ_ARENA_COUNTS,
                "error": 'No questions match that topic yet -- capture some first, or try "All topics."',
            },
            status_code=400,
        )

    request.session[_QUIZ_ARENA_SESSION_KEY] = {
        "question_ids": [q["question_id"] for q in questions],
        "index": 0,
        "correct": 0,
    }
    return RedirectResponse("/practice/quiz-arena/play", status_code=303)


@router.get("/quiz-arena/play", response_class=HTMLResponse)
async def quiz_arena_play(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    state = request.session.get(_QUIZ_ARENA_SESSION_KEY)
    if not state:
        return RedirectResponse("/practice/quiz-arena", status_code=303)
    if state["index"] >= len(state["question_ids"]):
        return RedirectResponse("/practice/quiz-arena/result", status_code=303)

    question_id = state["question_ids"][state["index"]]
    question = await service.get_question(pool, user_id, question_id)
    if question is None:
        # Deleted mid-quiz (from another tab, say) -- skip it rather than
        # error out of the whole session.
        state["index"] += 1
        request.session[_QUIZ_ARENA_SESSION_KEY] = state
        return RedirectResponse("/practice/quiz-arena/play", status_code=303)

    return templates.TemplateResponse(
        request,
        "practice/quiz_arena_play.html",
        {
            "question": question,
            "position": state["index"] + 1,
            "total": len(state["question_ids"]),
        },
    )


@router.post("/quiz-arena/answer")
async def quiz_arena_answer(
    request: Request,
    selected_index: int | None = Form(None),
    rating: int | None = Form(None),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    state = request.session.get(_QUIZ_ARENA_SESSION_KEY)
    if not state or state["index"] >= len(state["question_ids"]):
        return RedirectResponse("/practice/quiz-arena", status_code=303)

    question_id = state["question_ids"][state["index"]]
    question = await service.get_question(pool, user_id, question_id)
    correct = False
    if question is not None:
        if question["question_type"] == "multiple_choice" and selected_index is not None:
            _, correct = await service.record_mcq_attempt(pool, user_id, question_id, selected_index)
        elif rating is not None:
            await service.record_attempt(pool, user_id, question_id, rating)
            correct = rating >= 4

    state["correct"] += 1 if correct else 0
    state["index"] += 1
    request.session[_QUIZ_ARENA_SESSION_KEY] = state
    return RedirectResponse("/practice/quiz-arena/play", status_code=303)


@router.get("/quiz-arena/result", response_class=HTMLResponse)
async def quiz_arena_result(request: Request, user_id: int = Depends(require_user_id)):
    state = request.session.get(_QUIZ_ARENA_SESSION_KEY)
    if not state:
        return RedirectResponse("/practice/quiz-arena", status_code=303)

    total = len(state["question_ids"])
    correct = state["correct"]
    del request.session[_QUIZ_ARENA_SESSION_KEY]
    return templates.TemplateResponse(
        request,
        "practice/quiz_arena_result.html",
        {"correct": correct, "total": total},
    )
