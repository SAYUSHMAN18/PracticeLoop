from __future__ import annotations

import re

import bcrypt
from starlette.requests import Request

SESSION_USER_KEY = "user_id"
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72  # bcrypt's hard limit; 4.x raises instead of truncating

# Deliberately loose: one @, at least one dot in the domain, no spaces. The
# only real check for an address is whether mail to it is delivered -- this
# just rejects the obvious typos ("me", "me@localhost") the browser's own
# type="email" also catches, so a server-side POST can't slip past it.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvalidPassword(Exception):
    pass


class InvalidEmail(Exception):
    pass


def validate_email(email: str) -> None:
    if not (0 < len(email) <= 254) or not _EMAIL_RE.match(email):
        raise InvalidEmail("Enter a valid email address.")


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InvalidPassword(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise InvalidPassword(f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")


def hash_password(password: str) -> str:
    validate_password(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Oversized or otherwise malformed candidate password -- wrong, not a crash.
        return False


def current_user_id(request: Request) -> int | None:
    return request.session.get(SESSION_USER_KEY)


def log_in(request: Request, user_id: int) -> None:
    request.session[SESSION_USER_KEY] = user_id


def log_out(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)
