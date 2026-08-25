from __future__ import annotations

import asyncpg

from app.core.security import hash_password, verify_password


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    pass


async def create_user(pool: asyncpg.Pool, email: str, password: str, name: str) -> int:
    existing = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", email)
    if existing is not None:
        raise EmailAlreadyRegistered(email)

    async with pool.acquire() as conn:
        async with conn.transaction():
            user_id = await conn.fetchval(
                """INSERT INTO users (email, password_hash, name)
                   VALUES ($1, $2, $3) RETURNING user_id""",
                email,
                hash_password(password),
                name,
            )
            await conn.execute(
                "INSERT INTO profiles (user_id) VALUES ($1)",
                user_id,
            )

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
