from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.db import close_pool, get_pool
from app.core.security import current_user_id
from app.dashboard.router import router as dashboard_router
from app.practice.router import router as practice_router
from app.profile.router import router as profile_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="PracticeLoop", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(practice_router)
app.include_router(dashboard_router)


@app.get("/")
async def root(request: Request):
    destination = "/dashboard" if current_user_id(request) is not None else "/login"
    return RedirectResponse(destination)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
