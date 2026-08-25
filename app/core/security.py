from __future__ import annotations

import bcrypt
from starlette.requests import Request

SESSION_USER_KEY = "user_id"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def current_user_id(request: Request) -> int | None:
    return request.session.get(SESSION_USER_KEY)


def log_in(request: Request, user_id: int) -> None:
    request.session[SESSION_USER_KEY] = user_id


def log_out(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)
