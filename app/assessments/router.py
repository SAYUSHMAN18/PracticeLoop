from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.assessments import service
from app.core.db import get_pool
from app.core.deps import inject_current_user, require_user_id
from app.core.llm import is_configured as llm_is_configured
from app.core.llm_budget import LLMBudgetExceeded, consume_llm_budget
from app.core.logging import get_logger
from app.core.templates import templates
from app.profile import service as profile_service

router = APIRouter(dependencies=[Depends(inject_current_user)])
logger = get_logger(__name__)

# The in-progress quiz lives in the session, not the DB -- its questions
# are one-off and never meant to enter the spaced-repetition bank (see
# migrations/0014_diagnostic_attempts.sql). Same pattern practice/router.py
# already uses for the daily plan.
_SESSION_KEY = "diagnostic"


async def _render_index(request: Request, pool, user_id: int, *, error: str | None, status_code: int = 200):
    profile = await profile_service.get_profile(pool, user_id)
    attempts = await service.list_attempts(pool, user_id)
    return templates.TemplateResponse(
        request,
        "assessments/index.html",
        {
            "attempts": attempts,
            "profile": profile,
            "ai_available": llm_is_configured(),
            "proficiency_labels": profile_service.PROFICIENCY_LABELS,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/assessments", response_class=HTMLResponse)
async def assessments_index(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    return await _render_index(request, pool, user_id, error=None)


@router.post("/assessments/start")
async def start_diagnostic(
    request: Request,
    topic: str = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    topic = topic.strip()
    if not topic:
        return await _render_index(
            request, pool, user_id, error="Tell us what to test you on first.", status_code=400
        )

    if not llm_is_configured():
        return await _render_index(
            request,
            pool,
            user_id,
            error="Diagnostics need an AI provider configured -- ask your admin to set one up.",
            status_code=503,
        )

    try:
        await consume_llm_budget(pool, user_id)
    except LLMBudgetExceeded as exc:
        return await _render_index(request, pool, user_id, error=exc.detail, status_code=429)

    try:
        questions = await service.generate_diagnostic(topic, ai_available=True)
    except Exception:
        logger.warning("Diagnostic generation failed for topic=%r", topic, exc_info=True)
        return await _render_index(
            request,
            pool,
            user_id,
            error="Couldn't generate a diagnostic for that topic -- try rephrasing it.",
            status_code=502,
        )

    request.session[_SESSION_KEY] = {"topic": topic, "questions": questions}
    return RedirectResponse("/assessments/take", status_code=303)


@router.get("/assessments/take", response_class=HTMLResponse)
async def take_diagnostic(request: Request, user_id: int = Depends(require_user_id)):
    state = request.session.get(_SESSION_KEY)
    if not state:
        return RedirectResponse("/assessments", status_code=303)

    # correct_choice_index and subtopic are the answer key -- the quiz
    # page itself only needs the question text and its choices.
    public_questions = [{"question": q["question"], "choices": q["choices"]} for q in state["questions"]]
    return templates.TemplateResponse(
        request,
        "assessments/take.html",
        {"topic": state["topic"], "questions": public_questions},
    )


@router.post("/assessments/submit")
async def submit_diagnostic(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    state = request.session.get(_SESSION_KEY)
    if not state:
        return RedirectResponse("/assessments", status_code=303)

    form = await request.form()
    questions = state["questions"]
    correct_count = 0
    weak_subtopics = []
    for index, q in enumerate(questions):
        raw_answer = form.get(f"answer_{index}")
        try:
            selected_index = int(raw_answer) if raw_answer is not None else -1
        except ValueError:
            selected_index = -1
        if selected_index == q["correct_choice_index"]:
            correct_count += 1
        elif q["subtopic"]:
            weak_subtopics.append(q["subtopic"])

    result = await service.record_attempt(
        pool, user_id, state["topic"], correct_count, len(questions), weak_subtopics
    )
    del request.session[_SESSION_KEY]
    return RedirectResponse(f"/assessments/result/{result['attempt_id']}", status_code=303)


@router.get("/assessments/result/{attempt_id}", response_class=HTMLResponse)
async def diagnostic_result(
    attempt_id: int,
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    attempt = await service.get_attempt(pool, user_id, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "assessments/result.html",
        {"attempt": attempt, "proficiency_labels": profile_service.PROFICIENCY_LABELS},
    )
