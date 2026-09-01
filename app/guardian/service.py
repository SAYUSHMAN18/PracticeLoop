from __future__ import annotations

import secrets

import asyncpg

from app.classrooms.service import student_summary

# Guardian access is student-initiated, never teacher- or admin-initiated
# -- a student generates an invite link and shares it themselves (this
# app has no outbound-email infrastructure, so "copy this link and send
# it yourself" is the honest mechanism here, not a simulated email send).
# guardian_user_id only gets set the moment someone actually opens the
# link while logged in and accepts it -- that's the real consent event,
# not the link's creation.


class InviteNotFound(Exception):
    pass


class CannotGuardSelf(Exception):
    pass


def _generate_token() -> str:
    return secrets.token_urlsafe(24)


async def create_invite(pool: asyncpg.Pool, student_user_id: int) -> dict:
    token = _generate_token()
    link_id = await pool.fetchval(
        "INSERT INTO guardian_links (student_user_id, invite_token) VALUES ($1, $2) RETURNING link_id",
        student_user_id,
        token,
    )
    return {"link_id": link_id, "invite_token": token}


async def list_invites_for_student(pool: asyncpg.Pool, student_user_id: int) -> list[dict]:
    rows = await pool.fetch(
        """SELECT gl.link_id, gl.invite_token, gl.status, gl.created_at,
                  gl.accepted_at, u.name AS guardian_name
           FROM guardian_links gl
           LEFT JOIN users u ON u.user_id = gl.guardian_user_id
           WHERE gl.student_user_id = $1
           ORDER BY gl.created_at DESC""",
        student_user_id,
    )
    return [dict(r) for r in rows]


async def revoke_invite(pool: asyncpg.Pool, student_user_id: int, link_id: int) -> None:
    row = await pool.fetchrow(
        "SELECT link_id FROM guardian_links WHERE link_id = $1 AND student_user_id = $2",
        link_id,
        student_user_id,
    )
    if row is None:
        raise InviteNotFound(link_id)
    await pool.execute("UPDATE guardian_links SET status = 'revoked' WHERE link_id = $1", link_id)


async def get_invite_preview(pool: asyncpg.Pool, invite_token: str) -> dict:
    """For the accept page -- who's inviting, before the guardian commits
    to accepting. Doesn't require login, so it can't leak anything beyond
    the inviting student's own display name."""
    row = await pool.fetchrow(
        """SELECT gl.link_id, gl.status, u.name AS student_name
           FROM guardian_links gl JOIN users u ON u.user_id = gl.student_user_id
           WHERE gl.invite_token = $1""",
        invite_token,
    )
    if row is None:
        raise InviteNotFound(invite_token)
    return dict(row)


async def accept_invite(pool: asyncpg.Pool, guardian_user_id: int, invite_token: str) -> dict:
    row = await pool.fetchrow(
        "SELECT link_id, student_user_id, status FROM guardian_links WHERE invite_token = $1", invite_token
    )
    if row is None or row["status"] != "pending":
        raise InviteNotFound(invite_token)
    if row["student_user_id"] == guardian_user_id:
        raise CannotGuardSelf()

    await pool.execute(
        """UPDATE guardian_links SET guardian_user_id = $2, status = 'accepted', accepted_at = now()
           WHERE link_id = $1""",
        row["link_id"],
        guardian_user_id,
    )
    return {"student_user_id": row["student_user_id"]}


async def list_students_for_guardian(pool: asyncpg.Pool, guardian_user_id: int) -> list[dict]:
    rows = await pool.fetch(
        """SELECT u.user_id, u.name, gl.accepted_at
           FROM guardian_links gl JOIN users u ON u.user_id = gl.student_user_id
           WHERE gl.guardian_user_id = $1 AND gl.status = 'accepted'
           ORDER BY gl.accepted_at DESC""",
        guardian_user_id,
    )
    students = []
    for row in rows:
        streak, xp, completed_paths = await student_summary(pool, row["user_id"])
        students.append(
            {
                "user_id": row["user_id"],
                "name": row["name"],
                "accepted_at": row["accepted_at"],
                "streak": streak,
                "level": xp["level"],
                "total_xp": xp["total_xp"],
                "completed_paths": completed_paths,
            }
        )
    return students
