from __future__ import annotations

import asyncpg

from app.core.security import hash_password, verify_password


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    pass


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


async def authenticate(pool: asyncpg.Pool, email: str, password: str) -> int:
    row = await pool.fetchrow(
        "SELECT user_id, password_hash FROM users WHERE email = $1", email
    )
    if row is None or not verify_password(password, row["password_hash"]):
        raise InvalidCredentials(email)

    return row["user_id"]


async def get_user(pool: asyncpg.Pool, user_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT user_id, email, name FROM users WHERE user_id = $1", user_id
    )


async def get_user_by_email(pool: asyncpg.Pool, email: str) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT user_id, email, name FROM users WHERE email = $1", email
    )
