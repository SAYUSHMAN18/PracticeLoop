"""Apply pending migrations/NNNN_name.sql files, tracked in a
schema_migrations table -- replaces the old "recreate the database on every
schema change" workflow with something that's safe to run against a live one.

Usage:
    python scripts/migrate.py
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.core.config import settings  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
_FILENAME_RE = re.compile(r"^(\d+)_.+\.sql$")


def _discover_migrations() -> list[tuple[int, str, Path]]:
    migrations = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        match = _FILENAME_RE.match(path.name)
        if not match:
            raise ValueError(f"Migration filename doesn't match NNNN_name.sql: {path.name}")
        migrations.append((int(match.group(1)), path.stem, path))
    migrations.sort(key=lambda m: m[0])
    return migrations


async def apply_pending_migrations(conn: asyncpg.Connection) -> list[str]:
    """Applies every not-yet-recorded migration in ascending version order,
    each in its own transaction. Returns the names of migrations actually
    applied (empty if the database was already current)."""
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version    integer PRIMARY KEY,
               name       text NOT NULL,
               applied_at timestamptz NOT NULL DEFAULT now()
           )"""
    )
    applied_versions = {row["version"] for row in await conn.fetch("SELECT version FROM schema_migrations")}

    applied_names = []
    for version, name, path in _discover_migrations():
        if version in applied_versions:
            continue
        async with conn.transaction():
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute("INSERT INTO schema_migrations (version, name) VALUES ($1, $2)", version, name)
        applied_names.append(name)

    return applied_names


async def main() -> None:
    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        applied = await apply_pending_migrations(conn)
    finally:
        await conn.close()

    if applied:
        for name in applied:
            print(f"Applied {name}")
    else:
        print("Nothing to apply -- database is up to date.")


if __name__ == "__main__":
    asyncio.run(main())
