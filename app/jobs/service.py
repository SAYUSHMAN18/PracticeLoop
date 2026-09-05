from __future__ import annotations

import asyncpg

from app.core.config import settings
from app.core.logging import get_logger
from app.jobs.scoring import keyword_fit_score
from app.jobs.sources import SOURCES, RawListing

logger = get_logger(__name__)

_SEARCH_MAX_RESULTS = 10


async def search_live_listings(keywords: str, location: str = "") -> list[RawListing]:
    """An on-demand search against every configured source, for a caller
    that isn't discover_for_user's own per-student cron pass -- right now
    that's a teacher looking for real internships/roles to share with a
    classroom (app/classrooms/service.py's share_opportunity). Returns []
    (not an error) when nothing's configured, same as every source
    already does on its own."""
    results: list[RawListing] = []
    for fetch in SOURCES:
        try:
            results.extend(await fetch(keywords=keywords, location=location, max_results=_SEARCH_MAX_RESULTS))
        except Exception:
            logger.exception("Live job search failed for source=%s keywords=%r", fetch.__name__, keywords)
    return results


async def discover_for_user(pool: asyncpg.Pool, user_id: int, target_role: str, resume_text: str) -> int:
    """Fetches from every configured source for one user's target role,
    scores each result, and stores it. Returns how many *new* listings were
    stored (duplicates from a retried or overlapping run insert nothing).

    One source failing (timeout, quota, bad response) doesn't stop the
    others -- three of four searches succeeding still delivers a partial
    digest, which is more useful than none.
    """
    stored = 0
    for fetch in SOURCES:
        try:
            raw_listings = await fetch(keywords=target_role, max_results=settings.jobs_max_listings_per_user)
        except Exception:
            logger.exception("Job source %s failed for user_id=%s", fetch.__name__, user_id)
            continue

        for raw in raw_listings:
            score = keyword_fit_score(raw, resume_text)
            inserted_id = await pool.fetchval(
                """INSERT INTO job_listings
                       (user_id, source, external_id, title, company, location,
                        description, url, fit_score, fit_method)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'keyword')
                   ON CONFLICT (user_id, source, external_id) DO NOTHING
                   RETURNING listing_id""",
                user_id,
                raw.source,
                raw.external_id,
                raw.title,
                raw.company,
                raw.location,
                raw.description,
                raw.url,
                score,
            )
            if inserted_id is not None:
                stored += 1

    return stored


async def run_discovery(pool: asyncpg.Pool) -> int:
    """Called by the token-protected cron endpoint. Every user with a
    target_role set gets a discovery pass; the whole run is persisted as a
    job_runs row regardless of outcome -- a scheduled job that silently
    stops looks exactly like a quiet market, and the only way to tell the
    difference is a record that says whether it actually ran.
    """
    run_id = await pool.fetchval("INSERT INTO job_runs (status) VALUES ('running') RETURNING run_id")

    users_processed = 0
    users_failed = 0
    listings_found = 0
    run_error: str | None = None

    try:
        candidates = await pool.fetch(
            """SELECT u.user_id, p.target_role, p.resume_text
               FROM users u JOIN profiles p ON p.user_id = u.user_id
               WHERE p.target_role != ''"""
        )
        for row in candidates:
            try:
                listings_found += await discover_for_user(
                    pool, row["user_id"], row["target_role"], row["resume_text"]
                )
                users_processed += 1
            except Exception:
                users_failed += 1
                logger.exception("Discovery failed for user_id=%s", row["user_id"])

        if users_failed == 0:
            status = "success"
        elif users_processed > 0:
            status = "partial"
        else:
            status = "failed"
    except Exception as exc:
        logger.exception("Discovery run %s crashed before processing any user", run_id)
        status = "failed"
        run_error = str(exc)

    await pool.execute(
        """UPDATE job_runs
           SET finished_at = now(), status = $2, users_processed = $3,
               listings_found = $4, error = $5
           WHERE run_id = $1""",
        run_id,
        status,
        users_processed,
        listings_found,
        run_error,
    )
    return run_id


async def get_run(pool: asyncpg.Pool, run_id: int) -> asyncpg.Record:
    return await pool.fetchrow("SELECT * FROM job_runs WHERE run_id = $1", run_id)


async def list_recent_runs(pool: asyncpg.Pool, limit: int = 20) -> list[asyncpg.Record]:
    return await pool.fetch("SELECT * FROM job_runs ORDER BY started_at DESC LIMIT $1", limit)


async def list_listings(pool: asyncpg.Pool, user_id: int) -> list[asyncpg.Record]:
    return await pool.fetch(
        """SELECT * FROM job_listings WHERE user_id = $1
           ORDER BY fit_score DESC NULLS LAST, discovered_at DESC""",
        user_id,
    )
