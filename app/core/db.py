from __future__ import annotations

from typing import Optional

import asyncpg
from pgvector.asyncpg import register_vector

from app.core.config import settings

_pool: Optional[asyncpg.Pool] = None


async def _init_connection(connection: asyncpg.Connection) -> None:
    await register_vector(connection)


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
