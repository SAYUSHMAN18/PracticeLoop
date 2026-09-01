from __future__ import annotations

import json

import asyncpg
from pgvector.asyncpg import register_vector

from app.core.config import settings

_pool: asyncpg.Pool | None = None


async def _init_connection(connection: asyncpg.Connection) -> None:
    await register_vector(connection)
    # Without this, jsonb columns round-trip as plain str -- every query
    # site touching one (learning_lessons.content, questions.choices) would
    # otherwise need its own json.loads/json.dumps. One codec here instead
    # of that scattered everywhere it's used.
    await connection.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text"
    )


async def get_pool() -> asyncpg.Pool:
    global _pool

    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=1,
            max_size=10,
            init=_init_connection,
        )

    return _pool


async def close_pool() -> None:
    global _pool

    if _pool is not None:
        await _pool.close()
        _pool = None
