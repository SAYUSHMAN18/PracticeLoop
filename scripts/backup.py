"""Dump every table this app owns to a single timestamped JSON file.

Why not pg_dump: Render's free Postgres has no automated backups and no
psql/pg_dump on the web service's own container, so the realistic recovery
story for a free-tier deploy is "run this from anywhere that can reach
DATABASE_URL". It needs nothing but asyncpg, which is already a dependency.

This is a logical dump, not a physical one -- it restores content, not
sequences, indexes, or grants. Rebuild the schema with scripts/migrate.py
first, then load a dump into the empty database with --restore.

    python scripts/backup.py                       # write backups/practiceloop-<ts>.json
    python scripts/backup.py --out /path/file.json
    python scripts/backup.py --restore backups/practiceloop-<ts>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.core.config import settings  # noqa: E402

# schema_migrations is deliberately excluded: it describes the schema the
# dump was taken against, and on restore it's migrate.py that owns that
# table. Copying it in would let a restore claim migrations were applied
# to a database they weren't.
_SKIP_TABLES = {"schema_migrations"}


def _encode(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, memoryview | bytes):
        return value.hex()
    return str(value)


async def _table_names(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        """SELECT tablename FROM pg_tables
           WHERE schemaname = 'public' ORDER BY tablename"""
    )
    return [r["tablename"] for r in rows if r["tablename"] not in _SKIP_TABLES]


async def _column_types(conn: asyncpg.Connection, table: str) -> dict[str, str]:
    """Column -> Postgres type name, recorded in the dump so restore can put
    real Python types back. JSON has no datetime, so a timestamp round-trips
    as a string and asyncpg rejects it on the way back in unless something
    knows what it was supposed to be."""
    rows = await conn.fetch(
        """SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS type
           FROM pg_attribute a
           JOIN pg_class c ON c.oid = a.attrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relname = $1
             AND a.attnum > 0 AND NOT a.attisdropped""",
        table,
    )
    return {r["attname"]: r["type"] for r in rows}


async def _foreign_keys(conn: asyncpg.Connection) -> dict[str, list[str]]:
    """table -> the tables it references. Recorded at dump time so restore
    can insert parents before children: Postgres foreign keys are NOT
    DEFERRABLE by default, so `SET CONSTRAINTS ALL DEFERRED` does nothing
    for them and alphabetical table order fails the moment `applications`
    lands before `users`."""
    rows = await conn.fetch(
        """SELECT c.relname AS child, f.relname AS parent
           FROM pg_constraint con
           JOIN pg_class c ON c.oid = con.conrelid
           JOIN pg_class f ON f.oid = con.confrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE con.contype = 'f' AND n.nspname = 'public'"""
    )
    deps: dict[str, list[str]] = {}
    for r in rows:
        if r["child"] != r["parent"]:  # self-reference orders nothing
            deps.setdefault(r["child"], []).append(r["parent"])
    return deps


def _insert_order(tables: list[str], deps: dict[str, list[str]]) -> list[str]:
    """Parents first. A dependency cycle (none today) degrades to leaving the
    remaining tables in their original order rather than looping forever --
    the insert would fail loudly, which beats hanging."""
    remaining = list(tables)
    ordered: list[str] = []
    done: set[str] = set()
    while remaining:
        ready = [t for t in remaining if all(p in done or p not in tables for p in deps.get(t, []))]
        if not ready:
            ordered.extend(remaining)
            break
        ordered.extend(ready)
        done.update(ready)
        remaining = [t for t in remaining if t not in done]
    return ordered


async def dump(destination: Path) -> tuple[int, int]:
    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        payload: dict[str, list[dict]] = {}
        columns: dict[str, dict[str, str]] = {}
        for table in await _table_names(conn):
            rows = await conn.fetch(f'SELECT * FROM "{table}"')  # noqa: S608 -- name from pg_tables, not user input
            payload[table] = [dict(r) for r in rows]
            columns[table] = await _column_types(conn, table)
        foreign_keys = await _foreign_keys(conn)
    finally:
        await conn.close()

    destination.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "taken_at": datetime.now().astimezone().isoformat(),
        "columns": columns,
        "foreign_keys": foreign_keys,
        "tables": payload,
    }
    destination.write_text(json.dumps(body, default=_encode, indent=2), encoding="utf-8")
    return len(payload), sum(len(v) for v in payload.values())


def _decode(value, pg_type: str):
    """Inverse of _encode, driven by the column type recorded at dump time."""
    if value is None:
        return None
    if pg_type == "date":
        return date.fromisoformat(value)
    if pg_type.startswith("timestamp"):
        return datetime.fromisoformat(value)
    if pg_type == "bytea":
        return bytes.fromhex(value)
    if pg_type in ("jsonb", "json"):
        # This connection has no jsonb codec registered (app.core.db sets one
        # up, but that's the app's pool, not this script's bare connection),
        # so jsonb goes back as text plus an explicit cast in the INSERT.
        return json.dumps(value)
    if pg_type.startswith("vector"):
        return value if isinstance(value, str) else json.dumps(value)
    return value


def _placeholder(index: int, pg_type: str) -> str:
    """jsonb and vector both arrive as text and need Postgres to parse them;
    everything else asyncpg binds with the right type on its own."""
    if pg_type in ("jsonb", "json") or pg_type.startswith("vector"):
        return f"${index}::{pg_type}"
    return f"${index}"


async def restore(source: Path) -> int:
    body = json.loads(source.read_text(encoding="utf-8"))
    tables = body["tables"]
    columns = body.get("columns", {})
    order = _insert_order(list(tables), body.get("foreign_keys", {}))

    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        # One transaction: a half-restored database is worse than a failed
        # restore you can retry.
        async with conn.transaction():
            written = 0
            for table in order:
                rows = tables[table]
                if not rows:
                    continue
                types = columns.get(table, {})
                names = list(rows[0].keys())
                column_sql = ", ".join(f'"{c}"' for c in names)
                values_sql = ", ".join(_placeholder(i + 1, types.get(c, "text")) for i, c in enumerate(names))
                await conn.executemany(
                    f'INSERT INTO "{table}" ({column_sql}) VALUES ({values_sql})',  # noqa: S608
                    [[_decode(r[c], types.get(c, "text")) for c in names] for r in rows],
                )
                written += len(rows)

            # Every id column here is a serial/identity default. Restoring
            # explicit ids leaves those sequences at 1, so the next insert
            # after a restore collides with restored data on the primary key.
            await conn.execute(
                """DO $$
                   DECLARE r record;
                   BEGIN
                     FOR r IN
                       SELECT s.relname AS seq, t.relname AS tbl, a.attname AS col
                       FROM pg_class s
                       JOIN pg_depend d ON d.objid = s.oid
                       JOIN pg_class t ON t.oid = d.refobjid
                       JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
                       WHERE s.relkind = 'S'
                     LOOP
                       EXECUTE format(
                         'SELECT setval(%L, coalesce((SELECT max(%I) FROM %I), 0) + 1, false)',
                         r.seq, r.col, r.tbl
                       );
                     END LOOP;
                   END $$"""
            )
    finally:
        await conn.close()
    return written


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="where to write the dump")
    parser.add_argument("--restore", type=Path, help="load a dump into an empty database")
    args = parser.parse_args()

    if args.restore:
        written = await restore(args.restore)
        print(f"Restored {written} rows from {args.restore}")
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = args.out or Path("backups") / f"practiceloop-{stamp}.json"
    tables, rows = await dump(destination)
    print(f"Wrote {rows} rows across {tables} tables to {destination}")


if __name__ == "__main__":
    asyncio.run(main())
