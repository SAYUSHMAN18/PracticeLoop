"""Apply scripts/schema.sql over asyncpg -- the only prerequisite for setup
is Python, not a local `psql` install.

Usage:
    python scripts/init_db.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from app.core.config import settings

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


async def main() -> None:
    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        await conn.execute(SCHEMA_PATH.read_text())
        print("Schema applied.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
