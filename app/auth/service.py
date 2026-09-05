from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import asyncpg

from app.core.config import settings
from app.core.email import send_email
from app.core.security import hash_password, verify_password

# A syntactically valid bcrypt hash of an unguessable password, compared
# against on every login attempt for an email that doesn't exist -- so a
# nonexistent-email response takes the same bcrypt-bound time as a wrong
# password for a real one.
_DUMMY_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEeO0j5T6/vE9L8AGVqLC.CplfPfjJx1//G"

# A reset link is good for this long from the moment it's issued.
_RESET_TTL = timedelta(hours=1)


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class AccountLocked(Exception):
    """Too many consecutive failed logins. `minutes` is roughly how long
    is left on the lock."""

    def __init__(self, minutes: int):
        super().__init__(f"locked for ~{minutes} more minutes")
        self.minutes = minutes


class OAuthOnlyAccount(Exception):
    """This email belongs to an account with no password at all (created
    via Google Sign-In) -- there's no password to be wrong here, so the
    login form should say so instead of "incorrect email or password"."""

    def __init__(self, provider: str):
        super().__init__(f"sign in with {provider} instead")
        self.provider = provider


async def create_user(pool: asyncpg.Pool, email: str, password: str, name: str) -> int:
    password_hash = hash_password(password)  # raises InvalidPassword before touching the DB

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                user_id = await conn.fetchval(
                    """INSERT INTO users (email, password_hash, name)
                       VALUES ($1, $2, $3) RETURNING user_id""",
                    email,
                    password_hash,
                    name,
                )
                await conn.execute(
                    "INSERT INTO profiles (user_id) VALUES ($1)",
                    user_id,
                )
    except asyncpg.UniqueViolationError as exc:
        raise EmailAlreadyRegistered(email) from exc

    return user_id


async def get_or_create_oauth_user(
    pool: asyncpg.Pool, *, email: str, name: str, provider: str
) -> tuple[int, bool]:
    """Log in an OAuth-authenticated identity, matching by email. Returns
    (user_id, is_new).

    Matching an existing password account by email rather than refusing
    ("that email already has a password") is deliberate: the provider has
    already verified this address belongs to whoever is signing in, so
    it's the same person authenticating a second way, not an account
    takeover -- and it links their account to future Google sign-ins
    rather than leaving them stuck choosing one method forever.
    email_verified_at is stamped immediately for a brand-new account since
    the provider verified the address before PracticeLoop ever saw it."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT user_id FROM users WHERE email = $1 FOR UPDATE", email)
            if row is not None:
                await conn.execute(
                    "UPDATE users SET oauth_provider = $2 WHERE user_id = $1 AND oauth_provider = ''",
                    row["user_id"],
                    provider,
                )
                return row["user_id"], False

            user_id = await conn.fetchval(
                """INSERT INTO users (email, password_hash, name, oauth_provider, email_verified_at)
                   VALUES ($1, NULL, $2, $3, now()) RETURNING user_id""",
                email,
                name,
                provider,
            )
            await conn.execute("INSERT INTO profiles (user_id) VALUES ($1)", user_id)
            return user_id, True


async def authenticate(pool: asyncpg.Pool, email: str, password: str) -> int:
    row = await pool.fetchrow(
        "SELECT user_id, password_hash, oauth_provider, failed_login_count, locked_until "
        "FROM users WHERE email = $1",
        email,
    )
    now = datetime.now(timezone.utc)
    if row is not None and row["locked_until"] is not None and row["locked_until"] > now:
        raise AccountLocked(max(1, round((row["locked_until"] - now).total_seconds() / 60)))

    # Always run the bcrypt comparison against *some* hash -- for a
    # nonexistent email, and equally for a real OAuth-only account (whose
    # password_hash is NULL), or a timing difference would leak which of
    # the three this is before the OAuthOnlyAccount check below even runs.
    password_hash = (row["password_hash"] if row is not None else None) or _DUMMY_HASH
    password_ok = verify_password(password, password_hash)

    if row is not None and row["password_hash"] is None:
        raise OAuthOnlyAccount(row["oauth_provider"] or "another sign-in method")

    if row is None or not password_ok:
        if row is not None:
            await _register_failed_login(pool, row["user_id"], row["failed_login_count"])
        raise InvalidCredentials(email)

    if row["failed_login_count"]:
        await pool.execute(
            "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE user_id = $1",
            row["user_id"],
        )
    return row["user_id"]


async def _register_failed_login(pool: asyncpg.Pool, user_id: int, current_count: int) -> None:
    """Bump the counter; at the threshold, also stamp a lock. A successful
    login (or a reset) clears both."""
    new_count = current_count + 1
    if new_count >= settings.login_lockout_threshold:
        await pool.execute(
            "UPDATE users SET failed_login_count = $2, "
            "locked_until = now() + ($3 || ' minutes')::interval WHERE user_id = $1",
            user_id,
            new_count,
            str(settings.login_lockout_minutes),
        )
    else:
        await pool.execute("UPDATE users SET failed_login_count = $2 WHERE user_id = $1", user_id, new_count)


async def get_user(pool: asyncpg.Pool, user_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT user_id, email, name, role, email_verified_at FROM users WHERE user_id = $1", user_id
    )


async def get_user_by_email(pool: asyncpg.Pool, email: str) -> asyncpg.Record | None:
    return await pool.fetchrow("SELECT user_id, email, name, role FROM users WHERE email = $1", email)


# --- password reset -------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_password_reset_token(pool: asyncpg.Pool, email: str) -> str | None:
    """A fresh single-use token for this email's account, or None if no
    such account exists -- the caller shows the same "check your email"
    either way, so this never reveals which."""
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", email)
    if user_id is None:
        return None
    token = secrets.token_urlsafe(32)
    await pool.execute(
        "INSERT INTO password_reset_tokens (token_hash, user_id) VALUES ($1, $2)",
        _hash_token(token),
        user_id,
    )
    return token


async def consume_password_reset_token(pool: asyncpg.Pool, token: str, new_password: str) -> int | None:
    """Validate and spend the token, then set the new password -- all in
    one transaction. Returns the user_id, or None if the token is unknown,
    already used, or older than an hour. Raises InvalidPassword (from
    hash_password) if the new password fails the length rules.

    Spending it also deletes every other pending reset for that user and
    clears any login lockout."""
    token_hash = _hash_token(token)
    row = await pool.fetchrow(
        "SELECT user_id, created_at, used_at FROM password_reset_tokens WHERE token_hash = $1",
        token_hash,
    )
    if row is None or row["used_at"] is not None:
        return None
    if datetime.now(timezone.utc) - row["created_at"] > _RESET_TTL:
        return None

    password_hash = hash_password(new_password)  # raises InvalidPassword
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE password_reset_tokens SET used_at = now() WHERE token_hash = $1", token_hash
            )
            await conn.execute(
                "DELETE FROM password_reset_tokens WHERE user_id = $1 AND used_at IS NULL",
                row["user_id"],
            )
            await conn.execute(
                "UPDATE users SET password_hash = $2, failed_login_count = 0, locked_until = NULL "
                "WHERE user_id = $1",
                row["user_id"],
                password_hash,
            )
    return row["user_id"]


# --- email verification --------------------------------------------------


async def create_email_verification_token(pool: asyncpg.Pool, user_id: int) -> str:
    """A fresh verification token. Any earlier unused one for this user is
    dropped so only the newest link works (matters for "resend")."""
    token = secrets.token_urlsafe(32)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM email_verification_tokens WHERE user_id = $1 AND used_at IS NULL", user_id
            )
            await conn.execute(
                "INSERT INTO email_verification_tokens (token_hash, user_id) VALUES ($1, $2)",
                _hash_token(token),
                user_id,
            )
    return token


async def consume_email_verification_token(pool: asyncpg.Pool, token: str) -> int | None:
    """Mark the account verified. Returns the user_id, or None if the token
    is unknown or already used. No expiry -- an unverified account isn't a
    live security risk, so a late click still works."""
    token_hash = _hash_token(token)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT user_id, used_at FROM email_verification_tokens WHERE token_hash = $1 FOR UPDATE",
                token_hash,
            )
            if row is None or row["used_at"] is not None:
                return None
            await conn.execute(
                "UPDATE email_verification_tokens SET used_at = now() WHERE token_hash = $1", token_hash
            )
            await conn.execute(
                "UPDATE users SET email_verified_at = coalesce(email_verified_at, now()) WHERE user_id = $1",
                row["user_id"],
            )
    return row["user_id"]


async def send_verification_email(pool: asyncpg.Pool, user_id: int, email: str) -> None:
    """Best-effort -- a send failure is logged, never raised: signup and
    "resend" must not 500 because SMTP hiccuped."""
    token = await create_email_verification_token(pool, user_id)
    url = f"{settings.public_base_url.rstrip('/')}/verify-email?token={token}"
    await send_email(
        email,
        "Confirm your PracticeLoop email",
        "Welcome to PracticeLoop. Confirm this is your address so we can send "
        "you review reminders:\n\n"
        f"{url}\n\n"
        "You can use the app before confirming -- this just switches on the "
        "reminder emails.",
    )


_VALID_ROLES = {"student", "teacher"}


async def set_role(pool: asyncpg.Pool, user_id: int, role: str) -> None:
    """Self-declared, not verified -- there's no institutional identity
    check behind "teacher" here, matching this app's personal-scale
    single-tenant-plus-explicit-consent model. It only unlocks the
    classroom-creation UI; it never grants access to anyone else's data
    by itself (every cross-user view is gated by a join code or an
    accepted guardian invite, not by role)."""
    if role not in _VALID_ROLES:
        raise ValueError(f"invalid role: {role!r}")
    await pool.execute("UPDATE users SET role = $2 WHERE user_id = $1", user_id, role)
