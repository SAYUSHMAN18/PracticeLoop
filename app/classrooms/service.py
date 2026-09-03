from __future__ import annotations

import secrets
import string

import asyncpg

from app.core.moderation import check as moderate
from app.gamification.service import get_xp_summary
from app.practice.service import streak_days

_JOIN_CODE_ALPHABET = string.ascii_uppercase + string.digits
_JOIN_CODE_LENGTH = 6
_MAX_JOIN_CODE_ATTEMPTS = 5


class ClassroomNotFound(Exception):
    pass


class InvalidJoinCode(Exception):
    pass


class NameRejected(Exception):
    """A moderation check flagged the classroom name."""


def _generate_join_code() -> str:
    return "".join(secrets.choice(_JOIN_CODE_ALPHABET) for _ in range(_JOIN_CODE_LENGTH))


async def create_classroom(pool: asyncpg.Pool, teacher_user_id: int, name: str) -> dict:
    # Students see this name; screen it before it's stored. Fails open --
    # no LLM configured means every name passes.
    if (await moderate(name)).hard_block:
        raise NameRejected("That classroom name was flagged. Use a plain description of the class.")

    for _ in range(_MAX_JOIN_CODE_ATTEMPTS):
        join_code = _generate_join_code()
        try:
            classroom_id = await pool.fetchval(
                """INSERT INTO classrooms (teacher_user_id, name, join_code)
                   VALUES ($1, $2, $3) RETURNING classroom_id""",
                teacher_user_id,
                name,
                join_code,
            )
            return {"classroom_id": classroom_id, "join_code": join_code}
        except asyncpg.UniqueViolationError:
            continue  # extremely unlikely collision on a 6-char code -- just retry with a new one
    raise RuntimeError("Couldn't generate a unique join code -- try again.")


async def list_classrooms_for_teacher(pool: asyncpg.Pool, teacher_user_id: int) -> list[dict]:
    rows = await pool.fetch(
        """SELECT c.classroom_id, c.name, c.join_code, c.created_at, count(m.student_user_id) AS student_count
           FROM classrooms c
           LEFT JOIN classroom_members m ON m.classroom_id = c.classroom_id
           WHERE c.teacher_user_id = $1
           GROUP BY c.classroom_id
           ORDER BY c.created_at DESC""",
        teacher_user_id,
    )
    return [dict(r) for r in rows]


async def get_classroom_for_teacher(pool: asyncpg.Pool, teacher_user_id: int, classroom_id: int) -> dict:
    row = await pool.fetchrow(
        """SELECT classroom_id, name, join_code, created_at
           FROM classrooms WHERE classroom_id = $1 AND teacher_user_id = $2""",
        classroom_id,
        teacher_user_id,
    )
    if row is None:
        raise ClassroomNotFound(classroom_id)
    return dict(row)


async def get_roster(pool: asyncpg.Pool, teacher_user_id: int, classroom_id: int) -> list[dict]:
    """Ownership-checked via get_classroom_for_teacher below -- every
    stat here is a summary a teacher legitimately needs (streak, level,
    paths finished), never raw content (no mentor chat, no diagnostic
    weak-subtopic detail, no document contents)."""
    # raises ClassroomNotFound if not owned
    await get_classroom_for_teacher(pool, teacher_user_id, classroom_id)

    members = await pool.fetch(
        """SELECT u.user_id, u.name, u.email, m.joined_at
           FROM classroom_members m JOIN users u ON u.user_id = m.student_user_id
           WHERE m.classroom_id = $1
           ORDER BY m.joined_at""",
        classroom_id,
    )

    roster = []
    for member in members:
        student_id = member["user_id"]
        streak, xp, completed_paths = await student_summary(pool, student_id)
        roster.append(
            {
                "user_id": student_id,
                "name": member["name"],
                "email": member["email"],
                "joined_at": member["joined_at"],
                "streak": streak,
                "level": xp["level"],
                "total_xp": xp["total_xp"],
                "completed_paths": completed_paths,
            }
        )
    return roster


async def student_summary(pool: asyncpg.Pool, student_user_id: int) -> tuple[int, dict, int]:
    """Shared with app/guardian/service.py -- same summary-level stats
    (streak, XP/level, completed-paths count), same reasoning for why
    that's the right level of detail to expose to a teacher or a
    guardian: real signal, never raw content."""
    streak = await streak_days(pool, student_user_id)
    xp = await get_xp_summary(pool, student_user_id)
    completed_paths = await pool.fetchval(
        """SELECT count(*) FROM (
               SELECT p.path_id
               FROM learning_paths p
               JOIN learning_modules m ON m.path_id = p.path_id
               JOIN learning_units u ON u.module_id = m.module_id
               JOIN learning_lessons l ON l.unit_id = u.unit_id
               WHERE p.user_id = $1
               GROUP BY p.path_id
               HAVING count(*) = count(l.completed_at)
           ) AS fully_complete""",
        student_user_id,
    )
    return streak, xp, completed_paths


async def join_classroom(pool: asyncpg.Pool, student_user_id: int, join_code: str) -> dict:
    classroom = await pool.fetchrow(
        "SELECT classroom_id, name FROM classrooms WHERE join_code = $1", join_code.strip().upper()
    )
    if classroom is None:
        raise InvalidJoinCode(join_code)

    await pool.execute(
        """INSERT INTO classroom_members (classroom_id, student_user_id)
           VALUES ($1, $2) ON CONFLICT (classroom_id, student_user_id) DO NOTHING""",
        classroom["classroom_id"],
        student_user_id,
    )
    return dict(classroom)


async def list_classrooms_for_student(pool: asyncpg.Pool, student_user_id: int) -> list[dict]:
    rows = await pool.fetch(
        """SELECT c.classroom_id, c.name, t.name AS teacher_name, m.joined_at
           FROM classroom_members m
           JOIN classrooms c ON c.classroom_id = m.classroom_id
           JOIN users t ON t.user_id = c.teacher_user_id
           WHERE m.student_user_id = $1
           ORDER BY m.joined_at DESC""",
        student_user_id,
    )
    return [dict(r) for r in rows]


async def create_assignment(
    pool: asyncpg.Pool,
    teacher_user_id: int,
    classroom_id: int,
    title: str,
    description: str,
    topic: str,
    due_date,
) -> int:
    classroom = await get_classroom_for_teacher(pool, teacher_user_id, classroom_id)  # ownership check

    # Students see the title and get a notification with it -- screen it
    # (the description is theirs to write freely). Fails open.
    if (await moderate(title)).hard_block:
        raise NameRejected("That assignment title was flagged. Try rewording it.")

    assignment_id = await pool.fetchval(
        """INSERT INTO assignments (classroom_id, title, description, topic, due_date)
           VALUES ($1, $2, $3, $4, $5) RETURNING assignment_id""",
        classroom_id,
        title,
        description,
        topic,
        due_date,
    )

    from app.notifications.service import notify_classroom_members  # local import: avoids a load-time cycle

    await notify_classroom_members(
        pool,
        classroom_id,
        "assignment_created",
        f"New assignment in {classroom['name']}: {title}",
        body=description,
        link="/classrooms",
    )
    return assignment_id


async def list_assignments_for_classroom(
    pool: asyncpg.Pool, teacher_user_id: int, classroom_id: int
) -> list[dict]:
    await get_classroom_for_teacher(pool, teacher_user_id, classroom_id)  # ownership check
    rows = await pool.fetch(
        "SELECT assignment_id, title, description, topic, due_date, created_at "
        "FROM assignments WHERE classroom_id = $1 ORDER BY due_date NULLS LAST, created_at DESC",
        classroom_id,
    )
    return [dict(r) for r in rows]


async def list_assignments_for_student(pool: asyncpg.Pool, student_user_id: int) -> list[dict]:
    """Only assignments from classrooms this student actually joined --
    the join to classroom_members is the ownership check here, not a
    separate lookup."""
    rows = await pool.fetch(
        """SELECT a.assignment_id, a.title, a.description, a.topic, a.due_date, c.name AS classroom_name
           FROM assignments a
           JOIN classroom_members m ON m.classroom_id = a.classroom_id
           JOIN classrooms c ON c.classroom_id = a.classroom_id
           WHERE m.student_user_id = $1
           ORDER BY a.due_date NULLS LAST, a.created_at DESC""",
        student_user_id,
    )
    return [dict(r) for r in rows]


async def delete_classroom(pool: asyncpg.Pool, teacher_user_id: int, classroom_id: int) -> None:
    row = await pool.fetchrow(
        "SELECT classroom_id FROM classrooms WHERE classroom_id = $1 AND teacher_user_id = $2",
        classroom_id,
        teacher_user_id,
    )
    if row is None:
        raise ClassroomNotFound(classroom_id)
    await pool.execute("DELETE FROM classrooms WHERE classroom_id = $1", classroom_id)
