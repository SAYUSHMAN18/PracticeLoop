from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import db as db_module
from app.core.config import settings

TEST_DB_NAME = "practiceloop_test"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "scripts" / "schema.sql"


def _admin_dsn() -> str:
    # database_url with the database name swapped for the maintenance "postgres" db
    base, _, _ = settings.database_url.rpartition("/")
    return f"{base}/postgres"


def _test_dsn() -> str:
    base, _, _ = settings.database_url.rpartition("/")
    return f"{base}/{TEST_DB_NAME}"


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def _use_test_database():
    """Create a throwaway practiceloop_test database for the whole test
    session and point settings.database_url at it, so tests never touch
    whatever database a developer has running for manual use.

    Runs on pytest-asyncio's session-scoped loop (not a throwaway
    asyncio.run() loop) deliberately: app.core.db.get_pool()'s asyncpg pool
    is created lazily on whatever loop is active when a test first hits it,
    and asyncpg pools/connections can't be used or closed from a different
    loop than the one that created them.
    """
    conn = await asyncpg.connect(dsn=_admin_dsn())
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
        await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()

    conn = await asyncpg.connect(dsn=_test_dsn())
    try:
        await conn.execute(SCHEMA_PATH.read_text())
    finally:
        await conn.close()

    settings.database_url = _test_dsn()
    # The login/signup rate limiter is per-IP; httpx's ASGITransport gives
    # every test the same client IP, so it must be off here or the Nth
    # signup across the whole test session starts 429ing.
    settings.disable_rate_limits = True

    yield

    await db_module.close_pool()  # DROP DATABASE fails while it holds open connections

    conn = await asyncpg.connect(dsn=_admin_dsn())
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
    finally:
        await conn.close()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables():
    """Truncate between tests instead of recreating the schema each time."""
    pool = await db_module.get_pool()
    yield
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE users, profiles, questions, attempts RESTART IDENTITY CASCADE")


@pytest_asyncio.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def signup(
    client: AsyncClient, email: str, password: str = "testpassword123", name: str = "Test"
) -> None:
    response = await client.post(
        "/signup", data={"name": name, "email": email, "password": password}
    )
    assert response.status_code == 303, response.text
