from __future__ import annotations

import secrets
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.core.config import settings
from app.core.db import get_pool
from app.core.deps import require_user_id
from app.core.llm_budget import require_llm_budget
from app.core.logging import get_logger
from app.core.templates import templates
from app.jobs import applications, gap_analysis, interview_prep, service

logger = get_logger(__name__)

router = APIRouter(prefix="/jobs")


def _serialize_run(run) -> dict:
    return {
        "run_id": run["run_id"],
        "status": run["status"],
        "started_at": run["started_at"].isoformat(),
        "finished_at": run["finished_at"].isoformat() if run["finished_at"] else None,
        "users_processed": run["users_processed"],
        "listings_found": run["listings_found"],
        "error": run["error"],
    }


@router.post("/cron/discover")
async def cron_discover(request: Request, pool=Depends(get_pool)):
    configured = settings.jobs_cron_token.strip()
    if not configured:
        # Fail closed: an unset token must never be satisfiable by an empty
        # submitted one, so there's no "disabled" state that's also open.
        raise HTTPException(status_code=503, detail="Job discovery isn't configured on this instance.")

    submitted = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(submitted, configured):
        raise HTTPException(status_code=401, detail="Invalid or missing cron token.")

    run_id = await service.run_discovery(pool)
    run = await service.get_run(pool, run_id)
    # A non-2xx here is what makes the GitHub Actions workflow fail (and
    # email the owner) on a bad run, instead of a scheduled job silently
    # going quiet with nothing to notice it.
    status_code = 200 if run["status"] in ("success", "partial") else 500
    return JSONResponse(_serialize_run(run), status_code=status_code)


@router.get("/runs", response_class=HTMLResponse)
async def runs_page(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    runs = await service.list_recent_runs(pool)
    return templates.TemplateResponse(request, "jobs/runs.html", {"runs": runs})


@router.get("", response_class=HTMLResponse)
async def listings_page(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    listings = await service.list_listings(pool, user_id)
    return templates.TemplateResponse(request, "jobs/listings.html", {"listings": listings})


@router.post("/applications")
async def create_application(
    company: str = Form(...),
    role: str = Form(...),
    listing_id: int | None = Form(None),
    fit_score: int | None = Form(None),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    await applications.create_application(pool, user_id, company, role, listing_id, fit_score)
    return RedirectResponse("/jobs/applications", status_code=303)


@router.get("/applications", response_class=HTMLResponse)
async def applications_page(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    apps = await applications.list_applications(pool, user_id)
    stats = await applications.funnel_stats(pool, user_id)
    follow_ups = await applications.due_follow_ups(pool, user_id)
    stale = await applications.stale_applications(pool, user_id)
    return templates.TemplateResponse(
        request,
        "jobs/applications.html",
        {"applications": apps, "stats": stats, "follow_ups": follow_ups, "stale": stale},
    )


@router.post("/applications/{application_id}/status")
async def update_application_status(
    application_id: int,
    status: Literal["applied", "interviewing", "offer", "rejected", "withdrawn"] = Form(...),
    interview_at: str | None = Form(None),
    notes: str | None = Form(None),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    # A blank <input type="datetime-local"> submits "" (not omitted), and
    # FastAPI's own `datetime | None` coercion 422s on that rather than
    # treating it as None -- the common case here is "just change status,
    # leave the interview date alone", so this has to accept the field
    # loosely and parse it ourselves.
    parsed_interview_at = datetime.fromisoformat(interview_at) if interview_at else None

    try:
        await applications.update_status(pool, user_id, application_id, status, parsed_interview_at, notes)
    except applications.ApplicationNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return RedirectResponse("/jobs/applications", status_code=303)


@router.get("/gap-analysis", response_class=HTMLResponse)
async def gap_analysis_page(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    gaps = await gap_analysis.list_recent_gaps(pool, user_id)
    return templates.TemplateResponse(
        request, "jobs/gap_analysis.html", {"gaps": gaps, "error": None, "deck_message": None}
    )


@router.post("/gap-analysis", response_class=HTMLResponse)
async def run_gap_analysis(
    request: Request,
    jd_text: str = Form(...),
    listing_id: int | None = Form(None),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
    _budget: None = Depends(require_llm_budget),
):
    try:
        await gap_analysis.analyze_gap(pool, user_id, jd_text, listing_id)
    except Exception as exc:
        logger.warning("Gap analysis failed: %s", exc)
        gaps = await gap_analysis.list_recent_gaps(pool, user_id)
        return templates.TemplateResponse(
            request,
            "jobs/gap_analysis.html",
            {
                "gaps": gaps,
                "error": f"Couldn't analyze that job description right now: {exc}",
                "deck_message": None,
            },
            status_code=502,
        )
    return RedirectResponse("/jobs/gap-analysis", status_code=303)


@router.post("/gap-analysis/generate-deck", response_class=HTMLResponse)
async def generate_deck(
    request: Request,
    gap_ids: list[int] = Form(...),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    # No single require_llm_budget dependency here -- this can issue several
    # LLM calls (one per ungenerated skill), each with its own budget check
    # inside generate_deck_from_gaps, so a five-skill selection correctly
    # costs up to five against the daily count, not one.
    result = await gap_analysis.generate_deck_from_gaps(pool, user_id, gap_ids)
    gaps = await gap_analysis.list_recent_gaps(pool, user_id)

    message = f"Generated {result['generated']} new practice question(s)."
    if result["skipped_existing"]:
        message += f" {result['skipped_existing']} already had a close match in your bank."
    if result["budget_exhausted"]:
        message += " Stopped early -- today's AI generation budget is used up."

    return templates.TemplateResponse(
        request, "jobs/gap_analysis.html", {"gaps": gaps, "error": None, "deck_message": message}
    )


@router.get("/applications/{application_id}/deck", response_class=HTMLResponse)
async def company_deck_page(
    request: Request,
    application_id: int,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    application = await applications.get_application(pool, user_id, application_id)
    if application is None:
        raise HTTPException(status_code=404)

    deck = await interview_prep.get_company_deck(pool, user_id, application_id)
    return templates.TemplateResponse(
        request, "jobs/company_deck.html", {"application": application, "deck": deck}
    )


@router.post("/applications/{application_id}/debrief")
async def submit_debrief(
    application_id: int,
    questions_asked: str = Form(...),
    notes: str = Form(""),
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    application = await applications.get_application(pool, user_id, application_id)
    if application is None:
        raise HTTPException(status_code=404)

    await interview_prep.log_debrief(pool, user_id, application_id, questions_asked, notes)
    return RedirectResponse("/jobs/applications", status_code=303)
