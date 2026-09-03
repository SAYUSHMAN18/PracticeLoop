from __future__ import annotations

import asyncio
import json

import asyncpg

from app.core.json_extraction import extract_first_json_value
from app.core.llm import generate
from app.core.llm_budget import consume_llm_budget
from app.core.logging import get_logger
from app.gamification.service import award_xp

logger = get_logger(__name__)

_MAX_MILESTONES = 8
_XP_MILESTONE = 8
_XP_PROJECT_SUBMITTED = 30  # bigger than a lesson (15) -- a finished project is a bigger unit of work

_IDEA_PROMPT = """Suggest one project for a student working on: "{topic}"

Output strict JSON only, no markdown fences, no commentary, in exactly this shape:
{{
  "title": "a short project title",
  "brief": "2-4 sentences describing what to build and why it demonstrates the skill",
  "milestones": ["milestone title", "milestone title", "..."]
}}

Give 3-6 milestones, ordered from first step to last.
"""

_FEEDBACK_PROMPT = """A student submitted this project.

Project: "{title}"
Brief: {brief}

Their submission:
{submission}
{link_line}

Give rubric feedback. Output strict JSON only, no markdown fences, no commentary, in
exactly this shape:
{{
  "completeness_score": 1-5,
  "quality_score": 1-5,
  "strengths": ["short point", "short point"],
  "improvements": ["short, actionable point", "short, actionable point"],
  "summary": "one or two sentence overall take"
}}
"""


class ProjectNotFound(Exception):
    pass


def _fallback_idea(topic: str) -> dict:
    """Same honesty call as every other AI-optional generator in this
    app -- no deterministic template can invent a real project brief for
    an arbitrary topic. A real, checkable-off milestone list instead of
    a dead end."""
    return {
        "title": topic[:120] or "New project",
        "brief": f'Build something that demonstrates what you know about "{topic}". '
        "Scope it yourself -- start small and expand once the basics work.",
        "milestones": [
            "Plan what you're going to build",
            "Build a first working version",
            "Test it and fix what's broken",
            "Write up what you built and what you learned",
        ],
    }


def _validate_idea(data: dict, topic: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError("not an object")
    title = str(data.get("title") or "").strip()
    brief = str(data.get("brief") or "").strip()
    milestones_raw = data.get("milestones")
    milestones = [
        str(m).strip() for m in (milestones_raw if isinstance(milestones_raw, list) else []) if str(m).strip()
    ][:_MAX_MILESTONES]
    if not title or not milestones:
        raise ValueError("missing title or milestones")
    return {"title": title, "brief": brief, "milestones": milestones}


async def generate_idea(topic: str, *, ai_available: bool) -> dict:
    if not ai_available:
        return _fallback_idea(topic)
    try:
        response = await generate(_IDEA_PROMPT.format(topic=topic.strip()), temperature=0.5, cacheable=True)
        data = json.loads(extract_first_json_value(response))
        return _validate_idea(data, topic)
    except Exception:
        logger.warning("Project idea generation failed, using the fallback idea", exc_info=True)
        return _fallback_idea(topic)


async def create_project(
    pool: asyncpg.Pool,
    user_id: int,
    title: str,
    brief: str,
    milestones: list[str],
    *,
    path_id: int | None = None,
) -> int:
    async with pool.acquire() as conn:
        async with conn.transaction():
            if path_id is not None:
                owned = await conn.fetchval(
                    "SELECT 1 FROM learning_paths WHERE path_id = $1 AND user_id = $2", path_id, user_id
                )
                if not owned:
                    path_id = None  # don't silently attach to someone else's path -- just drop the link

            project_id = await conn.fetchval(
                """INSERT INTO projects (user_id, path_id, title, brief)
                   VALUES ($1, $2, $3, $4) RETURNING project_id""",
                user_id,
                path_id,
                title,
                brief,
            )
            for position, milestone_title in enumerate(milestones):
                await conn.execute(
                    "INSERT INTO project_milestones (project_id, title, position) VALUES ($1, $2, $3)",
                    project_id,
                    milestone_title,
                    position,
                )
    return project_id


async def list_projects(pool: asyncpg.Pool, user_id: int) -> list[dict]:
    rows = await pool.fetch(
        """SELECT p.project_id, p.title, p.status, p.created_at, p.submitted_at,
                  count(m.milestone_id) AS total_milestones, count(m.completed_at) AS completed_milestones
           FROM projects p
           LEFT JOIN project_milestones m ON m.project_id = p.project_id
           WHERE p.user_id = $1
           GROUP BY p.project_id
           ORDER BY p.created_at DESC""",
        user_id,
    )
    result = []
    for row in rows:
        percent = (
            round(100 * row["completed_milestones"] / row["total_milestones"])
            if row["total_milestones"]
            else 0
        )
        result.append({**dict(row), "progress_percent": percent})
    return result


async def get_project(pool: asyncpg.Pool, user_id: int, project_id: int) -> dict:
    project = await pool.fetchrow(
        """SELECT project_id, path_id, title, brief, status, submission_text, submission_link,
                  feedback, created_at, submitted_at
           FROM projects WHERE project_id = $1 AND user_id = $2""",
        project_id,
        user_id,
    )
    if project is None:
        raise ProjectNotFound(project_id)

    milestones = await pool.fetch(
        "SELECT milestone_id, title, position, completed_at FROM project_milestones "
        "WHERE project_id = $1 ORDER BY position",
        project_id,
    )
    total = len(milestones)
    completed = sum(1 for m in milestones if m["completed_at"] is not None)
    return {
        **dict(project),
        "milestones": [dict(m) for m in milestones],
        "total_milestones": total,
        "completed_milestones": completed,
        "progress_percent": round(100 * completed / total) if total else 0,
    }


async def toggle_milestone(pool: asyncpg.Pool, user_id: int, project_id: int, milestone_id: int) -> bool:
    row = await pool.fetchrow(
        """UPDATE project_milestones m
           SET completed_at = CASE WHEN m.completed_at IS NULL THEN now() ELSE NULL END
           FROM projects p
           WHERE m.milestone_id = $1
             AND m.project_id = p.project_id
             AND p.project_id = $2
             AND p.user_id = $3
           RETURNING m.completed_at""",
        milestone_id,
        project_id,
        user_id,
    )
    if row is None:
        raise ProjectNotFound(project_id)

    completed = row["completed_at"] is not None
    if completed:
        await award_xp(pool, user_id, "project_milestone", milestone_id, _XP_MILESTONE)
    return completed


async def submit_project(
    pool: asyncpg.Pool,
    user_id: int,
    project_id: int,
    submission_text: str,
    submission_link: str,
    *,
    ai_available: bool,
) -> dict:
    project = await pool.fetchrow(
        "SELECT title, brief FROM projects WHERE project_id = $1 AND user_id = $2", project_id, user_id
    )
    if project is None:
        raise ProjectNotFound(project_id)

    feedback = None
    if ai_available:
        try:
            await consume_llm_budget(pool, user_id)
            link_line = f"Link: {submission_link}" if submission_link else ""
            prompt = _FEEDBACK_PROMPT.format(
                title=project["title"],
                brief=project["brief"],
                submission=submission_text,
                link_line=link_line,
            )
            response = await generate(prompt, temperature=0.4)
            data = json.loads(extract_first_json_value(response))
            feedback = {
                "completeness_score": min(5, max(1, int(data.get("completeness_score", 3)))),
                "quality_score": min(5, max(1, int(data.get("quality_score", 3)))),
                "strengths": [str(s).strip() for s in (data.get("strengths") or [])][:5],
                "improvements": [str(s).strip() for s in (data.get("improvements") or [])][:5],
                "summary": str(data.get("summary") or "").strip(),
            }
        except Exception:
            logger.warning("Project feedback generation failed", exc_info=True)
            feedback = None

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """UPDATE projects SET status = 'submitted', submission_text = $2, submission_link = $3,
                       feedback = $4, submitted_at = now()
                   WHERE project_id = $1""",
                project_id,
                submission_text,
                submission_link,
                # jsonb type codec (app/core/db.py) encodes this dict itself -- don't json.dumps() it here too
                feedback,
            )
            await award_xp(conn, user_id, "project_submitted", project_id, _XP_PROJECT_SUBMITTED)

    return await get_project(pool, user_id, project_id)


async def get_portfolio(pool: asyncpg.Pool, user_id: int, *, streak_days: int) -> dict:
    """Aggregates existing data into one read-only "proof of learning"
    view -- no new tables, just pulling together what Phases 6-12
    already track. Private (own-view only) for now: a public/shareable
    link needs its own deliberate privacy design (what a stranger with
    the link can and can't see), not a boolean flag bolted on here."""
    from app.assessments.service import list_attempts as list_diagnostic_attempts
    from app.dashboard.service import topic_mastery
    from app.gamification.service import get_badges, get_xp_summary
    from app.learning_paths.service import list_paths

    projects, paths, diagnostics, mastery, badges, xp = await asyncio.gather(
        list_projects(pool, user_id),
        list_paths(pool, user_id),
        list_diagnostic_attempts(pool, user_id),
        topic_mastery(pool, user_id),
        get_badges(pool, user_id, streak_days=streak_days),
        get_xp_summary(pool, user_id),
    )

    return {
        "submitted_projects": [p for p in projects if p["status"] == "submitted"],
        "completed_paths": [p for p in paths if p["progress_percent"] == 100],
        "diagnostics": diagnostics,
        "top_mastery": sorted(mastery, key=lambda m: -m["mastery_score"])[:5],
        "earned_badges": [b for b in badges if b["earned"]],
        "xp": xp,
    }


async def delete_project(pool: asyncpg.Pool, user_id: int, project_id: int) -> None:
    row = await pool.fetchrow(
        "SELECT project_id FROM projects WHERE project_id = $1 AND user_id = $2", project_id, user_id
    )
    if row is None:
        raise ProjectNotFound(project_id)
    await pool.execute("DELETE FROM projects WHERE project_id = $1", project_id)
