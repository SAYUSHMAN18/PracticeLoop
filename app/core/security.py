from __future__ import annotations

import bcrypt
from starlette.requests import Request

SESSION_USER_KEY = "user_id"
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72  # bcrypt's hard limit; 4.x raises instead of truncating


class InvalidPassword(Exception):
    pass


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
