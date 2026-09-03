from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import service
from app.core.config import settings
from app.core.db import get_pool
from app.core.email import send_email
from app.core.logging import get_logger
from app.core.security import InvalidEmail, InvalidPassword, log_in, log_out, validate_email
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
    email = email.strip().lower()
    try:
        validate_email(email)
        user_id = await service.create_user(pool, email, password, name.strip())
    except service.EmailAlreadyRegistered:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": "That email is already registered."},
            status_code=400,
        )
    except (InvalidEmail, InvalidPassword) as exc:
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
async def login_form(request: Request, deleted: str = ""):
    return templates.TemplateResponse(
        request, "auth/login.html", {"error": None, "account_deleted": deleted == "1"}
    )


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    pool=Depends(get_pool),
):
    try:
        user_id = await service.authenticate(pool, email.strip().lower(), password)
    except service.AccountLocked as exc:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {
                "error": (
                    f"Too many failed attempts. Try again in about {exc.minutes} "
                    f"minute{'s' if exc.minutes != 1 else ''}, or reset your password."
                )
            },
            status_code=400,
        )
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


# --- password reset ------------------------------------------------------


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_form(request: Request):
    return templates.TemplateResponse(request, "auth/forgot_password.html", {"sent": False, "error": None})


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password(
    request: Request,
    email: str = Form(...),
    pool=Depends(get_pool),
):
    email = email.strip().lower()
    token = await service.create_password_reset_token(pool, email)
    if token is not None:
        url = f"{settings.public_base_url.rstrip('/')}/reset-password?token={token}"
        text = (
            "Someone asked to reset the password for this PracticeLoop account.\n\n"
            f"If it was you, open this link within the hour:\n\n{url}\n\n"
            "If it wasn't you, ignore this email -- nothing has changed."
        )
        await send_email(email, "Reset your PracticeLoop password", text)
    # Always the same response, whether or not the account exists.
    return templates.TemplateResponse(request, "auth/forgot_password.html", {"sent": True, "error": None})


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_form(request: Request, token: str = ""):
    # Full validation happens on POST; here just distinguish "no token at
    # all" (a bare visit) from "has a token, show the form".
    return templates.TemplateResponse(
        request, "auth/reset_password.html", {"token": token, "invalid": not token, "error": None}
    )


@router.post("/reset-password")
async def reset_password(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    pool=Depends(get_pool),
):
    try:
        user_id = await service.consume_password_reset_token(pool, token, password)
    except InvalidPassword as exc:
        return templates.TemplateResponse(
            request,
            "auth/reset_password.html",
            {"token": token, "invalid": False, "error": str(exc)},
            status_code=400,
        )

    if user_id is None:
        return templates.TemplateResponse(
            request,
            "auth/reset_password.html",
            {"token": token, "invalid": True, "error": None},
            status_code=400,
        )

    log_in(request, user_id)
    destination = "/welcome" if await needs_onboarding(pool, user_id) else "/dashboard"
    return RedirectResponse(destination, status_code=303)
