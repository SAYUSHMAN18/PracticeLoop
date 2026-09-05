from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import oauth, service
from app.core.config import settings
from app.core.db import get_pool
from app.core.deps import require_user_id
from app.core.email import send_email
from app.core.logging import get_logger
from app.core.security import InvalidEmail, InvalidPassword, log_in, log_out, validate_email
from app.core.templates import templates
from app.practice.service import seed_starter_deck
from app.profile.service import needs_onboarding

logger = get_logger(__name__)

router = APIRouter()

_OAUTH_STATE_SESSION_KEY = "oauth_state"


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

    try:
        await service.send_verification_email(pool, user_id, email)
    except Exception:
        logger.exception("Verification email failed for new user_id=%s", user_id)

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
    except service.OAuthOnlyAccount as exc:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": f"This account signs in with {exc.provider.title()} -- use the button below."},
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


# --- Google Sign-In -------------------------------------------------------


@router.get("/auth/google")
async def google_oauth_start(request: Request):
    if not oauth.is_configured():
        return RedirectResponse("/login", status_code=303)
    # A fresh value per attempt, checked against what the callback receives
    # -- without it, anything landing on /auth/google/callback with a code
    # of its own choosing (e.g. an attacker's own Google account, linked to
    # a CSRF'd session) could get logged into whoever's browser follows it.
    state = secrets.token_urlsafe(24)
    request.session[_OAUTH_STATE_SESSION_KEY] = state
    return RedirectResponse(oauth.build_authorize_url(state), status_code=303)


@router.get("/auth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    pool=Depends(get_pool),
):
    expected_state = request.session.pop(_OAUTH_STATE_SESSION_KEY, None)
    if error or not code or not state or not secrets.compare_digest(state, expected_state or ""):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "Google sign-in didn't complete -- try again.", "account_deleted": False},
            status_code=400,
        )

    try:
        userinfo = await oauth.exchange_code_for_userinfo(code)
    except oauth.OAuthExchangeFailed as exc:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": str(exc), "account_deleted": False},
            status_code=400,
        )

    user_id, _is_new = await service.get_or_create_oauth_user(
        pool, email=userinfo["email"], name=userinfo["name"], provider="google"
    )
    log_in(request, user_id)
    destination = "/welcome" if await needs_onboarding(pool, user_id) else "/dashboard"
    return RedirectResponse(destination, status_code=303)


# --- email verification -------------------------------------------------


@router.get("/verify-email")
async def verify_email(request: Request, token: str = "", pool=Depends(get_pool)):
    user_id = await service.consume_email_verification_token(pool, token) if token else None
    if user_id is None:
        return templates.TemplateResponse(
            request,
            "auth/verify_email.html",
            {"ok": False},
            status_code=400,
        )
    # If they clicked the link in a different browser, sign them in too --
    # a verified email and a live session is the least surprising outcome.
    log_in(request, user_id)
    return templates.TemplateResponse(request, "auth/verify_email.html", {"ok": True})


@router.post("/verify-email/resend")
async def resend_verification(
    request: Request,
    user_id: int = Depends(require_user_id),
    pool=Depends(get_pool),
):
    row = await pool.fetchrow("SELECT email, email_verified_at FROM users WHERE user_id = $1", user_id)
    if row is not None and row["email_verified_at"] is None:
        try:
            await service.send_verification_email(pool, user_id, row["email"])
        except Exception:
            logger.exception("Resend verification failed for user_id=%s", user_id)
    return RedirectResponse("/dashboard?verify_sent=1", status_code=303)


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
