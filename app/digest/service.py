"""The re-engagement digest.

The audit called a "you have N cards due" email the single most important
retention mechanism in this category, and the app had no way to send one
-- no delivery channel at all. Now there's email (app.core.email) and a
verified-address flag, so this is the job that ties them together.

Run by POST /cron/digest (see router) on a schedule. For each eligible
user it checks, in the user's own timezone:
  * do they have review cards due today, and
  * have they not practiced yet today
and if both, sends one short reminder and stamps last_digest_at so a
twice-daily cron can't double-send.

Eligible = email verified, not opted out, and not digested in the last
18 hours.
"""

from __future__ import annotations

import asyncpg
from itsdangerous import BadSignature, URLSafeSerializer

from app.core.config import settings
from app.core.email import send_email
from app.core.logging import get_logger
from app.core.usertime import canonical_zone_name, today_for
from app.practice.service import streak_days

logger = get_logger(__name__)

_UNSUB_SALT = "digest-unsubscribe"


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(settings.session_secret, salt=_UNSUB_SALT)


def unsubscribe_token(user_id: int) -> str:
    return _serializer().dumps(user_id)


def user_id_from_unsubscribe_token(token: str) -> int | None:
    try:
        value = _serializer().loads(token)
    except BadSignature:
        return None
    return value if isinstance(value, int) else None


async def _due_count(pool: asyncpg.Pool, user_id: int, today) -> int:
    return await pool.fetchval(
        """SELECT count(*) FROM questions q
           LEFT JOIN card_states cs ON cs.question_id = q.question_id
           WHERE q.user_id = $1 AND (cs.due IS NULL OR cs.due::date <= $2)""",
        user_id,
        today,
    )


async def _practiced_today(pool: asyncpg.Pool, user_id: int, tz_name: str, rollover: int, today) -> bool:
    return await pool.fetchval(
        """SELECT EXISTS (
               SELECT 1 FROM attempts
               WHERE user_id = $1
                 AND ((practiced_at AT TIME ZONE $2) - make_interval(hours => $3))::date = $4
           )""",
        user_id,
        canonical_zone_name(tz_name),
        rollover,
        today,
    )


def _body(name: str, due: int, streak: int, unsub_url: str, review_url: str) -> str:
    first = name.split()[0] if name.split() else "there"
    lines = [
        f"Hi {first},",
        "",
        f"You have {due} card{'s' if due != 1 else ''} due for review today.",
    ]
    if streak >= 2:
        lines.append(f"Your {streak}-day streak is still going -- a few minutes keeps it alive.")
    lines += [
        "",
        f"Review now: {review_url}",
        "",
        "---",
        f"Turn these off: {unsub_url}",
    ]
    return "\n".join(lines)


async def run_digest(pool: asyncpg.Pool) -> dict:
    base = settings.public_base_url.rstrip("/")
    review_url = f"{base}/practice/review"

    candidates = await pool.fetch(
        """SELECT u.user_id, u.name, u.email, p.timezone, p.day_rollover_hour
           FROM users u
           JOIN profiles p ON p.user_id = u.user_id
           WHERE u.email_verified_at IS NOT NULL
             AND NOT p.digest_opt_out
             AND (u.last_digest_at IS NULL OR u.last_digest_at < now() - interval '18 hours')"""
    )

    considered = len(candidates)
    sent = 0
    skipped_no_due = 0
    skipped_practiced = 0
    failed = 0

    for row in candidates:
        tz_name = row["timezone"] or ""
        rollover = row["day_rollover_hour"] or 0
        today = today_for(tz_name, rollover)

        due = await _due_count(pool, row["user_id"], today)
        if due == 0:
            skipped_no_due += 1
            continue
        if await _practiced_today(pool, row["user_id"], tz_name, rollover, today):
            skipped_practiced += 1
            continue

        streak = await streak_days(pool, row["user_id"])
        unsub_url = f"{base}/digest/unsubscribe?token={unsubscribe_token(row['user_id'])}"
        try:
            await send_email(
                row["email"],
                f"{due} card{'s' if due != 1 else ''} due on PracticeLoop",
                _body(row["name"], due, streak, unsub_url, review_url),
            )
            await pool.execute("UPDATE users SET last_digest_at = now() WHERE user_id = $1", row["user_id"])
            sent += 1
        except Exception:
            logger.exception("Digest send failed for user_id=%s", row["user_id"])
            failed += 1

    result = {
        "considered": considered,
        "sent": sent,
        "skipped_no_due": skipped_no_due,
        "skipped_practiced": skipped_practiced,
        "failed": failed,
    }
    logger.info("digest run: %s", result)
    return result
