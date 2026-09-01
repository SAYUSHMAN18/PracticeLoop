from __future__ import annotations

import asyncpg

_LIST_LIMIT = 20


class NotificationNotFound(Exception):
    pass


async def create(
    pool: asyncpg.Pool, user_id: int, kind: str, title: str, *, body: str = "", link: str = ""
) -> int:
    return await pool.fetchval(
        """INSERT INTO notifications (user_id, kind, title, body, link)
           VALUES ($1, $2, $3, $4, $5) RETURNING notification_id""",
        user_id,
        kind,
        title,
        body,
        link,
    )


async def notify_classroom_members(
    pool: asyncpg.Pool, classroom_id: int, kind: str, title: str, *, body: str = "", link: str = ""
) -> None:
    """One row per current member -- a student who joins *after* this
    fires simply doesn't get a backfilled notification for it, same as
    any real notification system."""
    member_ids = await pool.fetch(
        "SELECT student_user_id FROM classroom_members WHERE classroom_id = $1", classroom_id
    )
    for row in member_ids:
        await create(pool, row["student_user_id"], kind, title, body=body, link=link)


async def list_notifications(
    pool: asyncpg.Pool, user_id: int, *, limit: int = _LIST_LIMIT
) -> list[asyncpg.Record]:
    return await pool.fetch(
        """SELECT notification_id, kind, title, body, link, read_at, created_at
           FROM notifications WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2""",
        user_id,
        limit,
    )


async def unread_count(pool: asyncpg.Pool, user_id: int) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM notifications WHERE user_id = $1 AND read_at IS NULL", user_id
    )


async def mark_read(pool: asyncpg.Pool, user_id: int, notification_id: int) -> asyncpg.Record:
    """Returns the notification either way (already-read is a harmless
    no-op, not an error) -- callers use its `link` to send the student
    to whatever it was about. Only a notification_id that isn't theirs
    at all raises."""
    row = await pool.fetchrow(
        """UPDATE notifications SET read_at = now()
           WHERE notification_id = $1 AND user_id = $2 AND read_at IS NULL
           RETURNING notification_id, kind, title, body, link, read_at, created_at""",
        notification_id,
        user_id,
    )
    if row is not None:
        return row

    row = await pool.fetchrow(
        "SELECT notification_id, kind, title, body, link, read_at, created_at "
        "FROM notifications WHERE notification_id = $1 AND user_id = $2",
        notification_id,
        user_id,
    )
    if row is None:
        raise NotificationNotFound(notification_id)
    return row


async def mark_all_read(pool: asyncpg.Pool, user_id: int) -> None:
    await pool.execute(
        "UPDATE notifications SET read_at = now() WHERE user_id = $1 AND read_at IS NULL", user_id
    )
