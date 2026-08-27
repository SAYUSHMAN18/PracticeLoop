from __future__ import annotations

import asyncpg

from app.practice.service import create_question


async def upcoming_interviews(pool: asyncpg.Pool, user_id: int) -> list[asyncpg.Record]:
    """Applications with a future interview date, nearest first -- what the
    dashboard countdown is built from."""
    return await pool.fetch(
        """SELECT * FROM applications
           WHERE user_id = $1 AND interview_at IS NOT NULL AND interview_at > now()
           ORDER BY interview_at""",
        user_id,
    )


async def get_company_deck(pool: asyncpg.Pool, user_id: int, application_id: int) -> list[asyncpg.Record]:
    """The practice questions relevant to one tracked application's
    gap-analyzed skills -- a focused pre-interview review, not the normal
    spaced-repetition queue. Deliberately ignores next_review_at: cramming
    ahead of a known interview date is a different goal (peak recall on a
    known day) from long-term retention, so a question due next month
    still belongs in this list if it's relevant."""
    application = await pool.fetchrow(
        "SELECT listing_id FROM applications WHERE user_id = $1 AND application_id = $2",
        user_id,
        application_id,
    )
    if application is None or application["listing_id"] is None:
        return []

    skill_rows = await pool.fetch(
        "SELECT DISTINCT skill FROM job_skill_gaps WHERE user_id = $1 AND listing_id = $2",
        user_id,
        application["listing_id"],
    )
    if not skill_rows:
        return []

    questions = await pool.fetch("SELECT * FROM questions WHERE user_id = $1", user_id)
    skills_lower = [row["skill"].lower() for row in skill_rows]

    matched = []
    seen_ids: set[int] = set()
    for skill_lower in skills_lower:
        for q in questions:
            if q["question_id"] in seen_ids:
                continue
            if skill_lower in q["topic"].lower() or skill_lower in q["question"].lower():
                matched.append(q)
                seen_ids.add(q["question_id"])

    return matched


async def log_debrief(
    pool: asyncpg.Pool, user_id: int, application_id: int, questions_asked: str, notes: str
) -> int:
    """A structured debrief after the interview: what was actually asked
    becomes new practice cards, tagged with the company, immediately due
    (no attempts recorded yet). These are the highest-signal questions in
    the whole bank -- they're not a guess at what might come up, they're
    what actually did."""
    application = await pool.fetchrow(
        "SELECT company FROM applications WHERE user_id = $1 AND application_id = $2",
        user_id,
        application_id,
    )
    if application is None:
        raise ValueError(f"No application {application_id} for user {user_id}")

    await pool.execute(
        """UPDATE applications SET notes = COALESCE(NULLIF($3, ''), notes)
           WHERE user_id = $1 AND application_id = $2""",
        user_id,
        application_id,
        notes,
    )

    created = 0
    for line in questions_asked.splitlines():
        question_text = line.strip()
        if not question_text:
            continue
        await create_question(
            pool,
            user_id,
            {"question": question_text, "topic": application["company"], "company": application["company"]},
            source="interview_debrief",
        )
        created += 1

    return created
