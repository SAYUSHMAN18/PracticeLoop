from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import service
from app.core.db import get_pool
from app.core.logging import get_logger
from app.core.security import InvalidPassword, log_in, log_out
from app.core.templates import templates
from app.practice.service import seed_starter_deck
from app.profile.service import needs_onboarding

logger = get_logger(__name__)

router = APIRouter()


@router.get("/signup", response_class=HTMLResponse)
async def signup_form(request: Request):
    return templates.TemplateResponse(request, "auth/signup.html", {"error": None})


@router.post("/signup")
async def signup(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    seed_deck: bool = Form(False),
    pool=Depends(get_pool),
):
    try:
        user_id = await service.create_user(pool, email.strip().lower(), password, name.strip())
    except service.EmailAlreadyRegistered:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": "That email is already registered."},
            status_code=400,
        )
    except InvalidPassword as exc:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": str(exc)},
            status_code=400,
        )

    if seed_deck:
        try:
            await seed_starter_deck(pool, user_id)
        except Exception:
            logger.exception("Starter deck seeding failed for new user_id=%s", user_id)

    log_in(request, user_id)
    # A brand-new profile row is always onboarding_completed = false, but
    # asking anyway (rather than assuming) is what makes this correct even
    # if that default ever changes.
    destination = "/welcome" if await needs_onboarding(pool, user_id) else "/dashboard"
    return RedirectResponse(destination, status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "auth/login.html", {"error": None})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    pool=Depends(get_pool),
):
    try:
        user_id = await service.authenticate(pool, email.strip().lower(), password)
    except service.InvalidCredentials:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "Incorrect email or password."},
            status_code=400,
        )

    log_in(request, user_id)
    # Also catches accounts that existed before onboarding did -- they get
    # the same one-time prompt on their next login, not just new signups.
    destination = "/welcome" if await needs_onboarding(pool, user_id) else "/dashboard"
    return RedirectResponse(destination, status_code=303)


@router.post("/logout")
async def logout(request: Request):
    log_out(request)
    return RedirectResponse("/login", status_code=303)
