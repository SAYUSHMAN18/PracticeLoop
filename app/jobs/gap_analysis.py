from __future__ import annotations

import json

import asyncpg

from app.core.json_extraction import extract_first_json_value
from app.core.llm import generate

_SKILL_EXTRACTION_PROMPT = """Extract the specific skills, tools, languages, and
technologies required by the job description below. Output a strict JSON array
of short strings (e.g. ["Python", "Kubernetes", "system design"]) -- nothing
generic like "communication" or "team player". Output ONLY the JSON array, no
markdown fences, no explanation.

JOB DESCRIPTION:
{text}
"""

# Below this average confidence, a practiced skill still counts as
# "untested" rather than "proven" -- a single blackout-rated attempt
# shouldn't count as recall just because a matching question exists.
_RECALLED_CONFIDENCE_THRESHOLD = 3.0


async def extract_skills_from_jd(jd_text: str) -> list[str]:
    response = await generate(_SKILL_EXTRACTION_PROMPT.format(text=jd_text.strip()), temperature=0.0)
    data = json.loads(extract_first_json_value(response))
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of skills")
    return [str(item).strip() for item in data if str(item).strip()]


def _bucket_for_skill(
    skill: str, resume_text: str, questions: list[asyncpg.Record], recalled_question_ids: set[int]
) -> tuple[str, str]:
    skill_lower = skill.lower()
    on_resume = skill_lower in resume_text.lower()

    matching = [
        q for q in questions if skill_lower in q["topic"].lower() or skill_lower in q["question"].lower()
    ]
    recalled = any(q["question_id"] in recalled_question_ids for q in matching)

    if on_resume and recalled:
        return "proven", f"On your resume and recalled via {len(matching)} practiced question(s)."
    if on_resume:
        evidence = (
            "On your resume, but no practiced question tests it yet."
            if not matching
            else "On your resume and you have questions for it, but recall hasn't been strong yet."
        )
        return "untested", evidence
    return "missing", "Not found on your resume."


async def analyze_gap(
    pool: asyncpg.Pool, user_id: int, jd_text: str, listing_id: int | None = None
) -> list[dict]:
    """The feature: diffs a job description against what the resume claims
    and what practice history actually shows is recalled, producing three
    buckets. The middle one -- on the resume but never practiced, or
    practiced with weak recall -- is the insight nobody else can produce:
    it's what gets people rejected for skills their own resume lists.
    """
    profile = await pool.fetchrow("SELECT resume_text FROM profiles WHERE user_id = $1", user_id)
    resume_text = profile["resume_text"] if profile else ""

    questions = await pool.fetch(
        "SELECT question_id, question, topic FROM questions WHERE user_id = $1", user_id
    )
    recalled_rows = await pool.fetch(
        """SELECT question_id FROM attempts
           WHERE user_id = $1
           GROUP BY question_id
           HAVING avg(confidence_rating) >= $2""",
        user_id,
        _RECALLED_CONFIDENCE_THRESHOLD,
    )
    recalled_question_ids = {row["question_id"] for row in recalled_rows}

    skills = await extract_skills_from_jd(jd_text)

    results = []
    for skill in skills:
        bucket, evidence = _bucket_for_skill(skill, resume_text, questions, recalled_question_ids)
        gap_id = await pool.fetchval(
            """INSERT INTO job_skill_gaps (user_id, listing_id, skill, bucket, evidence)
               VALUES ($1, $2, $3, $4, $5) RETURNING gap_id""",
            user_id,
            listing_id,
            skill,
            bucket,
            evidence,
        )
        results.append({"gap_id": gap_id, "skill": skill, "bucket": bucket, "evidence": evidence})

    return results


async def list_recent_gaps(pool: asyncpg.Pool, user_id: int, limit: int = 50) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT * FROM job_skill_gaps WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
        user_id,
        limit,
    )
