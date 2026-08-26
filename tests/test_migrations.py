from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from migrate import apply_pending_migrations  # noqa: E402

from app.core.db import get_pool


async def test_rerunning_migrations_on_an_up_to_date_database_is_a_noop():
    """The whole point of tracking applied versions: a redeploy of an
    already-migrated database must not re-run 0001_baseline.sql (which
    would be harmless here since it's all IF NOT EXISTS, but a real future
    migration -- an ALTER TABLE, a data backfill -- would not be)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        applied = await apply_pending_migrations(conn)
    assert applied == []


async def test_a_new_migration_file_gets_applied_without_touching_baseline(tmp_path, monkeypatch):
    import migrate

    fake_migrations_dir = tmp_path / "migrations"
    fake_migrations_dir.mkdir()
    (fake_migrations_dir / "0001_baseline.sql").write_text(
        "CREATE TABLE IF NOT EXISTS users (user_id serial PRIMARY KEY);"
    )
    (fake_migrations_dir / "0002_add_marker_column.sql").write_text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS test_marker text;"
    )
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", fake_migrations_dir)

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Pretend 0001 was already applied in a previous deploy -- only 0002 should run.
        await conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (1, '0001_baseline') "
            "ON CONFLICT (version) DO NOTHING"
        )
        applied = await apply_pending_migrations(conn)
        assert applied == ["0002_add_marker_column"]

        column_exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name = 'test_marker')"
        )
        assert column_exists is True

        await conn.execute("ALTER TABLE users DROP COLUMN test_marker")
        await conn.execute("DELETE FROM schema_migrations WHERE version = 2")
