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
from app.learning_paths import service as learning_paths_service
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
        item_bank = await service.generate_diagnostic(topic, ai_available=True)
    except Exception:
        logger.warning("Diagnostic generation failed for topic=%r", topic, exc_info=True)
        return await _render_index(
            request,
            pool,
            user_id,
            error="Couldn't generate a diagnostic for that topic -- try rephrasing it.",
            status_code=502,
        )

    request.session[_SESSION_KEY] = {"topic": topic, "state": service.start_session(item_bank)}
    return RedirectResponse("/assessments/take", status_code=303)


@router.get("/assessments/take", response_class=HTMLResponse)
async def take_diagnostic(request: Request, user_id: int = Depends(require_user_id)):
    session_entry = request.session.get(_SESSION_KEY)
    if not session_entry:
        return RedirectResponse("/assessments", status_code=303)

    state = session_entry["state"]
    question = service.current_question(state)
    if question is None:
        # Shouldn't happen -- /assessments/submit redirects away the moment
        # a session completes -- but a stale/tampered session cookie is a
        # real input, not just a theoretical one, so fail back to start
        # rather than 500 on a None question.
        del request.session[_SESSION_KEY]
        return RedirectResponse("/assessments", status_code=303)

    return templates.TemplateResponse(
        request,
        "assessments/take.html",
        {
            "topic": session_entry["topic"],
            "question": question,
            "question_number": len(state["asked_indices"]) + 1,
            # min(): an item bank smaller than QUESTION_COUNT (a narrow
            # topic, or some generated questions dropped by validation)
            # still ends the diagnostic early -- the progress line
            # shouldn't promise a question count the pool can't deliver.
            "question_count": min(service.QUESTION_COUNT, len(state["pool"])),
        },
    )


@router.post("/assessments/submit")
async def submit_diagnostic(
    request: Request,
    answer: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    session_entry = request.session.get(_SESSION_KEY)
    if not session_entry:
        return RedirectResponse("/assessments", status_code=303)

    state = session_entry["state"]
    try:
        selected_index = int(answer)
    except ValueError:
        selected_index = -1
    service.answer_current_question(state, selected_index)

    if not service.is_complete(state):
        request.session[_SESSION_KEY] = session_entry  # persist the advanced state, tier, and next question
        return RedirectResponse("/assessments/take", status_code=303)

    correct_count, total_count, weak_subtopics = service.summarize(state)
    result = await service.record_attempt(
        pool, user_id, session_entry["topic"], correct_count, total_count, weak_subtopics
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
    paths = await learning_paths_service.list_paths(pool, user_id)
    return templates.TemplateResponse(
        request,
        "assessments/result.html",
        {
            "attempt": attempt,
            "proficiency_labels": profile_service.PROFICIENCY_LABELS,
            "paths": paths,
        },
    )


@router.post("/assessments/result/{attempt_id}/reinforce")
async def reinforce_from_result(
    attempt_id: int,
    request: Request,
    path_id: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    """Turns this diagnostic's weak subtopics into a focus module at the top
    of a learning path -- an existing one, or a new one named after the
    topic when `path_id` is blank.

    Budget is consumed the same way lesson content does it (try, and fall
    back to the deterministic unit if there's none left) rather than via
    require_llm_budget: a student out of budget should still get their
    measured gaps turned into a real, checkable plan, just without the
    AI-written lesson titles. A 429 here would strand the one page whose
    entire purpose is telling them what to do next.
    """
    attempt = await service.get_attempt(pool, user_id, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404)

    weak_subtopics = [s for s in (attempt["weak_subtopics"] or []) if s]
    if not weak_subtopics:
        return await _render_index(
            request,
            pool,
            user_id,
            error="That diagnostic didn't flag any weak subtopics -- nothing to build a focus unit from.",
            status_code=400,
        )

    ai_available = llm_is_configured()
    if ai_available:
        try:
            await consume_llm_budget(pool, user_id)
        except LLMBudgetExceeded:
            logger.info("LLM budget exhausted for remediation, using the fallback unit")
            ai_available = False

    if path_id.strip():
        try:
            target_path_id = int(path_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid path.") from exc
        try:
            await learning_paths_service.add_remediation_module(
                pool,
                user_id,
                target_path_id,
                topic=attempt["topic"],
                weak_subtopics=weak_subtopics,
                attempt_id=attempt_id,
                ai_available=ai_available,
            )
        except learning_paths_service.PathNotFound as exc:
            raise HTTPException(status_code=404) from exc
    else:
        target_path_id = await learning_paths_service.create_path_from_diagnostic(
            pool,
            user_id,
            topic=attempt["topic"],
            weak_subtopics=weak_subtopics,
            attempt_id=attempt_id,
            ai_available=ai_available,
        )

    return RedirectResponse(f"/learning-paths/{target_path_id}", status_code=303)
