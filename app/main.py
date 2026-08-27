from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth.router import router as auth_router
from app.core.config import settings, verify_production_config
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
from app.core.security import current_user_id
from app.core.templates import STATIC_DIR, templates
from app.dashboard.router import router as dashboard_router
from app.documents.router import router as documents_router
from app.jobs.router import router as jobs_router
from app.practice.router import router as practice_router
from app.profile.router import router as profile_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
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
app.include_router(jobs_router)
app.include_router(documents_router)


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


@app.get("/")
async def root(request: Request):
    destination = "/dashboard" if current_user_id(request) is not None else "/login"
    return RedirectResponse(destination)


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
