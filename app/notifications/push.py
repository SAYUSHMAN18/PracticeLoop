"""Web push -- an actual OS/browser notification, not just the in-app bell.

Same shape as app/core/email.py: one function, never raises for a
delivery problem (a push failing must not break whatever action
triggered the notification), and the real network call is synchronous
(pywebpush uses requests under the hood) so it's run off the event loop
via run_in_threadpool exactly like the SMTP backend does.

Requires VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY (scripts/gen_vapid_keys.py);
unconfigured just means send_push_to_user is a no-op, same as an unset
EMAIL_BACKEND.
"""

from __future__ import annotations

import json

import asyncpg
from pywebpush import WebPushException, webpush
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def is_configured() -> bool:
    return bool(settings.vapid_public_key.strip() and settings.vapid_private_key.strip())


def _send_one_sync(subscription_info: dict, payload: str) -> None:
    webpush(
        subscription_info=subscription_info,
        data=payload,
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": settings.vapid_subject},
    )


async def add_subscription(
    pool: asyncpg.Pool, user_id: int, *, endpoint: str, p256dh: str, auth: str
) -> None:
    """Upserts on endpoint (unique per browser installation) so re-enabling
    notifications in the same browser updates the existing row instead of
    duplicating it -- p256dh/auth can legitimately change if the browser
    ever rotates them."""
    await pool.execute(
        """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (endpoint) DO UPDATE SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth""",
        user_id,
        endpoint,
        p256dh,
        auth,
    )


async def remove_subscription(pool: asyncpg.Pool, user_id: int, endpoint: str) -> None:
    await pool.execute(
        "DELETE FROM push_subscriptions WHERE user_id = $1 AND endpoint = $2", user_id, endpoint
    )


async def send_push_to_user(pool: asyncpg.Pool, user_id: int, title: str, body: str, link: str = "") -> None:
    """Best-effort: pushes to every device this user has subscribed on,
    dropping any subscription the push service reports as gone (404/410 --
    the standard way a browser tells you it revoked one) so a stale
    subscription doesn't fail forever on every future notification."""
    if not is_configured():
        return

    payload = json.dumps({"title": title, "body": body, "link": link})
    subscriptions = await pool.fetch(
        "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = $1", user_id
    )
    for row in subscriptions:
        subscription_info = {
            "endpoint": row["endpoint"],
            "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
        }
        try:
            await run_in_threadpool(_send_one_sync, subscription_info, payload)
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (404, 410):
                await remove_subscription(pool, user_id, row["endpoint"])
            else:
                logger.warning("Push failed (status=%s) for user_id=%s", status, user_id)
        except Exception:
            logger.exception("Push failed for user_id=%s", user_id)
