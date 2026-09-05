from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.account.router import router as account_router
from app.admin.router import router as admin_router
from app.analytics.router import router as analytics_router
from app.assessments.router import router as assessments_router
from app.auth.router import router as auth_router
from app.classrooms.router import router as classrooms_router
from app.core.config import configure_error_reporting, settings, verify_production_config
from app.core.db import close_pool, get_pool
from app.core.deps import LoginRequired
from app.core.embedder import get_embedding_model, verify_embedding_dimension
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    MaxBodySizeMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    StaticCacheHeadersMiddleware,
)
from app.core.templates import STATIC_DIR, templates
from app.dashboard.router import router as dashboard_router
from app.decks.router import router as decks_router
from app.digest.router import router as digest_router
from app.documents.router import router as documents_router
from app.guardian.router import router as guardian_router
from app.jobs.router import router as jobs_router
from app.labs.router import router as labs_router
from app.learning_paths.router import router as learning_paths_router
from app.mentor.router import router as mentor_router
from app.notifications.router import router as notifications_router
from app.practice.router import router as practice_router
from app.profile.router import router as profile_router
from app.projects.router import router as projects_router
from app.public.router import router as public_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    configure_error_reporting()
    verify_production_config()

    await get_pool()
    verify_embedding_dimension()
    get_embedding_model()  # load it now, not on the first user's search request

    yield
    await close_pool()


app = FastAPI(title="PracticeLoop", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.app_env == "production",
    max_age=14 * 24 * 60 * 60,  # 14 days
)
app.add_middleware(
    RateLimitMiddleware,
    limits={}
    if settings.disable_rate_limits
    else {
        "/login": (10, 60.0),
        "/signup": (5, 60.0),
        "/forgot-password": (5, 300.0),
        "/reset-password": (10, 300.0),
    },
)
app.add_middleware(
    MaxBodySizeMiddleware,
    default_max_bytes=2 * 1024 * 1024,  # 2MB covers every text-only form
    path_overrides={
        "/profile": 11 * 1024 * 1024,  # resume upload's 10MB cap + overhead
        "/documents": 11 * 1024 * 1024,  # document upload's 10MB cap + overhead
    },
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(StaticCacheHeadersMiddleware)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(practice_router)
app.include_router(dashboard_router)
app.include_router(decks_router)
app.include_router(jobs_router)
app.include_router(documents_router)
app.include_router(learning_paths_router)
app.include_router(assessments_router)
app.include_router(mentor_router)
app.include_router(labs_router)
app.include_router(projects_router)
app.include_router(classrooms_router)
app.include_router(guardian_router)
app.include_router(notifications_router)
app.include_router(analytics_router)
app.include_router(account_router)
app.include_router(admin_router)
app.include_router(digest_router)
# Last: its "/" must not shadow anything, and it is the only public surface.
app.include_router(public_router)


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)
    # Every HTTPException reaching this handler was raised deliberately by
    # the app with a message meant for the user (413 oversized upload, 422
    # bad input, 429 over budget, ...) -- showing it beats a blanket "that's
    # on us" that both hides the real reason and, for something like a rate
    # limit, is simply wrong.
    detail = exc.detail if isinstance(exc.detail, str) else None
    return templates.TemplateResponse(request, "error.html", {"detail": detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return templates.TemplateResponse(request, "error.html", {}, status_code=500)


@app.get("/service-worker.js")
async def service_worker():
    # Served from the root, not /static/service-worker.js, deliberately:
    # a service worker's default scope is its own directory and below,
    # so serving it from /static/ would only ever let it control pages
    # under /static/ -- root-level pages are the entire point.
    return FileResponse(STATIC_DIR / "service-worker.js", media_type="text/javascript")


@app.get("/healthz")
async def healthz():
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
    except Exception as exc:
        return JSONResponse(
            {"status": "unhealthy", "component": "database", "error": str(exc)},
            status_code=503,
        )
    return {"status": "ok"}
