from __future__ import annotations

import json

import asyncpg

from app.core.embedder import embed_text_async
from app.core.json_extraction import extract_first_json_value
from app.core.llm import generate
from app.core.llm_budget import consume_llm_budget
from app.core.logging import get_logger
from app.gamification.service import award_xp

logger = get_logger(__name__)

_XP_LESSON_COMPLETE = 15

# The checkpoint_answer that _fallback_lesson_content writes when there is no
# LLM to draft a real one. Recognised in two places -- here it means "make
# this a self-rated review card, there is nothing real to grade against".
_LESSON_FALLBACK_ANSWER = "There's no AI-suggested answer yet -- write your own."

# Curated starting points for the Explore Subjects page -- Phase 6's
# "existing PracticeLoop template" source type. Straight from the plan's
# own example goals; a real catalog (per-subject browsing, more than a
# handful of entries) is more than this slice needs.
TEMPLATES = [
    {
        "id": "algebra-basics",
        "title": "Learn algebra from the beginning.",
        "blurb": "Start from the fundamentals and build up to solving real equations.",
    },
    {
        "id": "neet-biology",
        "title": "Prepare for NEET biology.",
        "blurb": "Cover the syllabus topic by topic, from cell biology through human physiology.",
    },
    {
        "id": "python-job-ready",
        "title": "Become job-ready in Python.",
        "blurb": "Core language, common libraries, and the patterns interviewers actually ask about.",
    },
    {
        "id": "english-speaking",
        "title": "Improve English speaking.",
        "blurb": "Build vocabulary and fluency for everyday and professional conversation.",
    },
    {
        "id": "personal-finance",
        "title": "Understand personal finance.",
        "blurb": "Budgeting, saving, credit, and investing basics -- no prior background assumed.",
    },
    {
        "id": "class-8-science",
        "title": "Prepare for my Class 8 science exam.",
        "blurb": "Work through the syllabus with practice checkpoints along the way.",
    },
]

_TEMPLATES_BY_ID = {t["id"]: t for t in TEMPLATES}


def get_template(template_id: str) -> dict | None:
    return _TEMPLATES_BY_ID.get(template_id)


# Caps on whatever the LLM proposes -- generous enough for a real course
# outline, tight enough that a model ignoring the prompt's own "3-5
# modules" instruction can't turn one goal into hundreds of DB rows.
_MAX_MODULES = 8
_MAX_UNITS_PER_MODULE = 6
_MAX_LESSONS_PER_UNIT = 8

_SKELETON_PROMPT = """Design a learning path for this goal: "{goal}"

Break it into 3-5 modules, ordered from foundational to advanced. Each module has 2-4
units. Each unit has 2-4 short lesson titles.

Output strict JSON only, no markdown fences, no commentary, in exactly this shape:
{{
  "path_title": "a short title for the whole path",
  "modules": [
    {{
      "title": "...",
      "description": "one sentence on what this module covers",
      "units": [
        {{
          "title": "...",
          "description": "one sentence on what this unit covers",
          "lessons": ["lesson title", "lesson title", "..."]
        }}
      ]
    }}
  ]
}}
"""


class PathNotFound(Exception):
    pass


def _fallback_skeleton(goal: str) -> dict:
    """No deterministic template can guess a real curriculum for an
    arbitrary goal -- same honesty call as flashcards.py's single-card
    fallback. This gives a real, checkable-off structure (not a dead
    end) rather than pretending to be a tailored course outline."""
    return {
        "path_title": goal[:120] or "New learning path",
        "modules": [
            {
                "title": "Getting started",
                "description": "A first pass to get oriented before going deeper.",
                "units": [
                    {
                        "title": "Foundations",
                        "description": "",
                        "lessons": [
                            "Get oriented with the topic",
                            "Learn the core concepts",
                            "Practice what you've learned",
                            "Review and identify weak spots",
                        ],
                    }
                ],
            }
        ],
    }


def _validate_skeleton(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("not an object")
    modules = data.get("modules")
    if not isinstance(modules, list) or not modules:
        raise ValueError("no modules")

    cleaned_modules = []
    for module in modules[:_MAX_MODULES]:
        if not isinstance(module, dict):
            continue
        module_title = str(module.get("title") or "").strip()
        if not module_title:
            continue
        units = module.get("units")
        cleaned_units = []
        for unit in (units if isinstance(units, list) else [])[:_MAX_UNITS_PER_MODULE]:
            if not isinstance(unit, dict):
                continue
            unit_title = str(unit.get("title") or "").strip()
            if not unit_title:
                continue
            lessons_raw = unit.get("lessons")
            lessons = [
                str(lesson).strip()
                for lesson in (lessons_raw if isinstance(lessons_raw, list) else [])
                if str(lesson).strip()
            ][:_MAX_LESSONS_PER_UNIT]
            if not lessons:
                continue
            cleaned_units.append(
                {
                    "title": unit_title,
                    "description": str(unit.get("description") or "").strip(),
                    "lessons": lessons,
                }
            )
        if cleaned_units:
            cleaned_modules.append(
                {
                    "title": module_title,
                    "description": str(module.get("description") or "").strip(),
                    "units": cleaned_units,
                }
            )

    if not cleaned_modules:
        raise ValueError("no usable modules after validation")

    return {
        "path_title": str(data.get("path_title") or "").strip() or "New learning path",
        "modules": cleaned_modules,
    }


async def _build_skeleton(goal: str, *, ai_available: bool) -> dict:
    if not ai_available:
        return _fallback_skeleton(goal)

    try:
        prompt = _SKELETON_PROMPT.format(goal=goal.strip())
        response = await generate(prompt, temperature=0.4)
        data = json.loads(extract_first_json_value(response))
        return _validate_skeleton(data)
    except Exception:
        logger.warning("Learning path skeleton generation failed, using the fallback skeleton", exc_info=True)
        return _fallback_skeleton(goal)


async def create_path(
    pool: asyncpg.Pool,
    user_id: int,
    goal: str,
    *,
    ai_available: bool,
    source_type: str = "goal",
    source_detail: str | None = None,
) -> int:
    """Turns a goal (typed text, or a template's own goal text) into a
    persisted modules -> units -> lessons skeleton, and returns the new
    path's id. One transaction: a path with only half its modules
    written because of a mid-insert error is worse than no path at all."""
    skeleton = await _build_skeleton(goal, ai_available=ai_available)

    async with pool.acquire() as conn:
        async with conn.transaction():
            path_id = await conn.fetchval(
                """INSERT INTO learning_paths (user_id, title, source_type, source_detail, ai_generated)
                   VALUES ($1, $2, $3, $4, $5) RETURNING path_id""",
                user_id,
                skeleton["path_title"],
                source_type,
                source_detail if source_detail is not None else goal,
                ai_available,
            )

            for module_position, module in enumerate(skeleton["modules"]):
                module_id = await conn.fetchval(
                    """INSERT INTO learning_modules (path_id, title, description, position)
                       VALUES ($1, $2, $3, $4) RETURNING module_id""",
                    path_id,
                    module["title"],
                    module["description"],
                    module_position,
                )
                for unit_position, unit in enumerate(module["units"]):
                    unit_id = await conn.fetchval(
                        """INSERT INTO learning_units (module_id, title, description, position)
                           VALUES ($1, $2, $3, $4) RETURNING unit_id""",
                        module_id,
                        unit["title"],
                        unit["description"],
                        unit_position,
                    )
                    for lesson_position, lesson_title in enumerate(unit["lessons"]):
                        await conn.execute(
                            """INSERT INTO learning_lessons (unit_id, title, position)
                               VALUES ($1, $2, $3)""",
                            unit_id,
                            lesson_title,
                            lesson_position,
                        )

    return path_id


async def list_paths(pool: asyncpg.Pool, user_id: int) -> list[dict]:
    """One row per path with a lesson-count/completed-count progress
    summary -- computed with one aggregate query across the whole join,
    not N+1 queries per path."""
    rows = await pool.fetch(
        """SELECT p.path_id, p.title, p.source_type, p.ai_generated, p.created_at,
                  count(l.lesson_id) AS lesson_count,
                  count(l.completed_at) AS completed_count
           FROM learning_paths p
           LEFT JOIN learning_modules m ON m.path_id = p.path_id
           LEFT JOIN learning_units u ON u.module_id = m.module_id
           LEFT JOIN learning_lessons l ON l.unit_id = u.unit_id
           WHERE p.user_id = $1
           GROUP BY p.path_id
           ORDER BY p.created_at DESC""",
        user_id,
    )
    result = []
    for row in rows:
        percent = round(100 * row["completed_count"] / row["lesson_count"]) if row["lesson_count"] else 0
        result.append({**dict(row), "progress_percent": percent})
    return result


async def get_path_detail(pool: asyncpg.Pool, user_id: int, path_id: int) -> dict:
    """Ownership-checked; returns the path nested as modules -> units ->
    lessons, each level ordered by its own position."""
    path = await pool.fetchrow(
        "SELECT path_id, title, source_type, source_detail, ai_generated, created_at "
        "FROM learning_paths WHERE path_id = $1 AND user_id = $2",
        path_id,
        user_id,
    )
    if path is None:
        raise PathNotFound(path_id)

    module_rows = await pool.fetch(
        "SELECT module_id, title, description, position, source_attempt_id FROM learning_modules "
        "WHERE path_id = $1 ORDER BY position",
        path_id,
    )
    unit_rows = await pool.fetch(
        """SELECT u.unit_id, u.module_id, u.title, u.description, u.position
           FROM learning_units u
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1
           ORDER BY u.position""",
        path_id,
    )
    lesson_rows = await pool.fetch(
        """SELECT l.lesson_id, l.unit_id, l.title, l.position, l.completed_at
           FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1
           ORDER BY m.position, u.position, l.position""",
        path_id,
    )

    lessons_by_unit: dict[int, list[dict]] = {}
    for row in lesson_rows:
        lessons_by_unit.setdefault(row["unit_id"], []).append(dict(row))

    units_by_module: dict[int, list[dict]] = {}
    for row in unit_rows:
        unit = dict(row)
        unit["lessons"] = lessons_by_unit.get(row["unit_id"], [])
        units_by_module.setdefault(row["module_id"], []).append(unit)

    modules = []
    for row in module_rows:
        module = dict(row)
        module["units"] = units_by_module.get(row["module_id"], [])
        modules.append(module)

    total_lessons = len(lesson_rows)
    completed_lessons = sum(1 for row in lesson_rows if row["completed_at"] is not None)

    lesson_ids = [row["lesson_id"] for row in lesson_rows]
    review_card_count = (
        await pool.fetchval(
            "SELECT count(*) FROM questions WHERE source_lesson_id = ANY($1::int[])", lesson_ids
        )
        if lesson_ids
        else 0
    )

    # "Resume" -- the first not-yet-complete lesson in true module/unit/
    # lesson reading order, or the last lesson if everything's done (so
    # the button still goes somewhere useful instead of disappearing).
    resume_lesson_id = next(
        (row["lesson_id"] for row in lesson_rows if row["completed_at"] is None),
        lesson_rows[-1]["lesson_id"] if lesson_rows else None,
    )

    return {
        **dict(path),
        "modules": modules,
        "total_lessons": total_lessons,
        "completed_lessons": completed_lessons,
        "progress_percent": round(100 * completed_lessons / total_lessons) if total_lessons else 0,
        "resume_lesson_id": resume_lesson_id,
        "review_card_count": review_card_count,
    }


# Phase 17: turning a diagnostic result into lessons. Bounded the same way
# the path skeleton is -- a diagnostic is 8 questions, so it can surface at
# most 8 weak subtopics, but the LLM is free to merge related ones and
# shouldn't be able to turn that into an unbounded lesson list either.
_MAX_REMEDIATION_SUBTOPICS = 8
_MAX_REMEDIATION_LESSONS = 12

_REMEDIATION_PROMPT = """A student just took a diagnostic quiz on "{topic}" and got these
subtopics wrong:

{subtopic_list}

Design a short focus unit that fixes exactly those gaps -- nothing else. Write 3-6 lesson
titles, ordered so earlier ones are prerequisites for later ones. Cover every subtopic
above; merge two into one lesson only when they're genuinely the same idea.

Output strict JSON only, no markdown fences, no commentary, in exactly this shape:
{{
  "unit_title": "a short title for this focus unit",
  "unit_description": "one sentence on what it fixes",
  "lessons": ["lesson title", "lesson title", "..."]
}}
"""


def _fallback_remediation(topic: str, weak_subtopics: list[str]) -> dict:
    """No LLM: one lesson per measured gap, named after the gap itself.
    That's a real, honest plan -- the subtopics came from the student's own
    wrong answers, not from a guess -- and each lesson's *content* still
    gets written on first open by get_lesson's own AI-or-fallback path, so
    nothing here pretends to be tailored material it isn't."""
    return {
        "unit_title": f"Weak spots in {topic}",
        "unit_description": "Built from the subtopics you missed on the diagnostic.",
        "lessons": [f"Review: {subtopic}" for subtopic in weak_subtopics],
    }


def _validate_remediation(data: dict, topic: str, weak_subtopics: list[str]) -> dict:
    if not isinstance(data, dict):
        raise ValueError("not an object")
    lessons_raw = data.get("lessons")
    lessons = [
        str(lesson).strip()
        for lesson in (lessons_raw if isinstance(lessons_raw, list) else [])
        if str(lesson).strip()
    ][:_MAX_REMEDIATION_LESSONS]
    if not lessons:
        raise ValueError("no usable lessons after validation")

    fallback = _fallback_remediation(topic, weak_subtopics)
    return {
        "unit_title": str(data.get("unit_title") or "").strip() or fallback["unit_title"],
        "unit_description": str(data.get("unit_description") or "").strip() or fallback["unit_description"],
        "lessons": lessons,
    }


async def _build_remediation(topic: str, weak_subtopics: list[str], *, ai_available: bool) -> dict:
    if not ai_available:
        return _fallback_remediation(topic, weak_subtopics)

    try:
        prompt = _REMEDIATION_PROMPT.format(
            topic=topic.strip(),
            subtopic_list="\n".join(f"- {s}" for s in weak_subtopics),
        )
        response = await generate(prompt, temperature=0.4)
        data = json.loads(extract_first_json_value(response))
        return _validate_remediation(data, topic, weak_subtopics)
    except Exception:
        logger.warning("Remediation unit generation failed, using the fallback unit", exc_info=True)
        return _fallback_remediation(topic, weak_subtopics)


async def _find_remediation_module(pool: asyncpg.Pool, path_id: int, attempt_id: int) -> int | None:
    return await pool.fetchval(
        "SELECT module_id FROM learning_modules WHERE path_id = $1 AND source_attempt_id = $2",
        path_id,
        attempt_id,
    )


async def add_remediation_module(
    pool: asyncpg.Pool,
    user_id: int,
    path_id: int,
    *,
    topic: str,
    weak_subtopics: list[str],
    attempt_id: int,
    ai_available: bool,
) -> int:
    """Inserts a focus module built from a diagnostic's weak subtopics at
    the *top* of an owned path, shifting everything already there down one
    position. Top, not bottom: the whole point of re-planning around a
    measurement is that the next thing studied is the thing just measured
    as weakest -- appending it after every existing module would bury it.

    Returns the module id. Idempotent per (path, diagnostic): the check
    below handles the ordinary repeat, and the partial unique index from
    migration 0021 is what makes that true even for two requests racing
    (a double-click issues both before either has inserted).
    """
    owned = await pool.fetchval(
        "SELECT path_id FROM learning_paths WHERE path_id = $1 AND user_id = $2", path_id, user_id
    )
    if owned is None:
        raise PathNotFound(path_id)

    existing = await _find_remediation_module(pool, path_id, attempt_id)
    if existing is not None:
        return existing

    subtopics = [s for s in weak_subtopics if s][:_MAX_REMEDIATION_SUBTOPICS]
    unit = await _build_remediation(topic, subtopics, ai_available=ai_available)

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Shift first so position 0 is free. Descending order isn't
                # needed -- there's no unique constraint on (path_id, position)
                # to transiently violate -- but the whole shift and the insert
                # share one transaction so a reader never sees two modules
                # claiming the same position.
                await conn.execute(
                    "UPDATE learning_modules SET position = position + 1 WHERE path_id = $1", path_id
                )
                module_id = await conn.fetchval(
                    """INSERT INTO learning_modules
                           (path_id, title, description, position, source_attempt_id)
                       VALUES ($1, $2, $3, 0, $4) RETURNING module_id""",
                    path_id,
                    f"Focus: {topic}",
                    "Added from your diagnostic result -- these are the subtopics you missed.",
                    attempt_id,
                )
                unit_id = await conn.fetchval(
                    """INSERT INTO learning_units (module_id, title, description, position)
                       VALUES ($1, $2, $3, 0) RETURNING unit_id""",
                    module_id,
                    unit["unit_title"],
                    unit["unit_description"],
                )
                for position, lesson_title in enumerate(unit["lessons"]):
                    await conn.execute(
                        "INSERT INTO learning_lessons (unit_id, title, position) VALUES ($1, $2, $3)",
                        unit_id,
                        lesson_title,
                        position,
                    )
    except asyncpg.UniqueViolationError:
        # Lost a race with a concurrent request for the same (path,
        # diagnostic) -- the index did its job, the whole transaction
        # (including the position shift) rolled back, and the winner's
        # module is the right answer to return. A double-click should
        # land the student on their focus module, not a 500.
        existing = await _find_remediation_module(pool, path_id, attempt_id)
        if existing is None:
            raise
        return existing

    return module_id


async def create_path_from_diagnostic(
    pool: asyncpg.Pool,
    user_id: int,
    *,
    topic: str,
    weak_subtopics: list[str],
    attempt_id: int,
    ai_available: bool,
) -> int:
    """A student with no path yet still needs somewhere for the focus
    module to land. Creates an empty path titled after the diagnostic's
    topic and puts the module in it -- deliberately *not* a full generated
    curriculum on top, which would bury the measured gaps under a module
    the student didn't ask for and hasn't been assessed on."""
    path_id = await pool.fetchval(
        """INSERT INTO learning_paths (user_id, title, source_type, source_detail, ai_generated)
           VALUES ($1, $2, 'diagnostic', $3, $4) RETURNING path_id""",
        user_id,
        topic,
        str(attempt_id),
        ai_available,
    )
    await add_remediation_module(
        pool,
        user_id,
        path_id,
        topic=topic,
        weak_subtopics=weak_subtopics,
        attempt_id=attempt_id,
        ai_available=ai_available,
    )
    return path_id


async def delete_path(pool: asyncpg.Pool, user_id: int, path_id: int) -> None:
    row = await pool.fetchrow(
        "SELECT path_id FROM learning_paths WHERE path_id = $1 AND user_id = $2", path_id, user_id
    )
    if row is None:
        raise PathNotFound(path_id)
    await pool.execute("DELETE FROM learning_paths WHERE path_id = $1", path_id)


_LESSON_CONTENT_PROMPT = """You are writing one short lesson for a learning path.

Path: "{path_title}"
Module: "{module_title}"
Unit: "{unit_title}"
Lesson: "{lesson_title}"

Write the lesson content. Output strict JSON only, no markdown fences, no commentary, in
exactly this shape:
{{
  "concept": "2-4 sentences explaining the concept plainly",
  "example": "one concrete worked example illustrating it",
  "checkpoint_question": "one short question to self-check understanding",
  "checkpoint_answer": "the answer to that question",
  "summary": "one sentence recapping the key takeaway"
}}
"""


def _fallback_lesson_content(lesson_title: str) -> dict:
    """Same honesty call as the path skeleton's own fallback -- no
    deterministic template can write real subject-matter content for an
    arbitrary lesson title. Real, structured, clearly-not-AI-tailored
    content instead of a dead end."""
    return {
        "concept": f'This lesson covers "{lesson_title}". Fill in your own notes here, '
        "or add an LLM provider key so PracticeLoop can draft this for you.",
        "example": "",
        "checkpoint_question": f'In your own words, what is the main idea of "{lesson_title}"?',
        "checkpoint_answer": _LESSON_FALLBACK_ANSWER,
        "summary": "",
    }


def _validate_lesson_content(data: dict, lesson_title: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError("not an object")
    fallback = _fallback_lesson_content(lesson_title)
    cleaned = {}
    for key, default in fallback.items():
        value = data.get(key)
        cleaned[key] = str(value).strip() if value else default
    if not cleaned["concept"]:
        raise ValueError("no concept explanation")
    return cleaned


async def _build_lesson_content(
    *, path_title: str, module_title: str, unit_title: str, lesson_title: str, ai_available: bool
) -> dict:
    if not ai_available:
        return _fallback_lesson_content(lesson_title)

    try:
        prompt = _LESSON_CONTENT_PROMPT.format(
            path_title=path_title, module_title=module_title, unit_title=unit_title, lesson_title=lesson_title
        )
        response = await generate(prompt, temperature=0.4)
        data = json.loads(extract_first_json_value(response))
        return _validate_lesson_content(data, lesson_title)
    except Exception:
        logger.warning("Lesson content generation failed, using the fallback content", exc_info=True)
        return _fallback_lesson_content(lesson_title)


async def get_lesson(
    pool: asyncpg.Pool, user_id: int, path_id: int, lesson_id: int, *, ai_available: bool
) -> dict:
    """Ownership-checked; returns the lesson with its content (generating
    and caching it on first open) plus its neighbors' ids for prev/next
    navigation within the path."""
    row = await pool.fetchrow(
        """SELECT l.lesson_id, l.title AS lesson_title, l.content, l.completed_at,
                  u.title AS unit_title, m.title AS module_title, p.path_id, p.title AS path_title
           FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           JOIN learning_paths p ON p.path_id = m.path_id
           WHERE l.lesson_id = $1 AND p.path_id = $2 AND p.user_id = $3""",
        lesson_id,
        path_id,
        user_id,
    )
    if row is None:
        raise PathNotFound(path_id)

    content = row["content"]
    if content is None:
        # Only a lesson opened for the first time (content is still null)
        # ever reaches an LLM call -- every later visit reads the cached
        # column below, so this doesn't gate the whole route behind the
        # daily budget the way create_path's *always*-one-call route can.
        # A student who's out of budget still gets the honest fallback
        # content, not a 429 for opening a lesson.
        lesson_ai_available = ai_available
        if lesson_ai_available:
            try:
                await consume_llm_budget(pool, user_id)
            except Exception:
                logger.info("LLM budget unavailable for lesson content, using the fallback content")
                lesson_ai_available = False

        content = await _build_lesson_content(
            path_title=row["path_title"],
            module_title=row["module_title"],
            unit_title=row["unit_title"],
            lesson_title=row["lesson_title"],
            ai_available=lesson_ai_available,
        )
        await pool.execute(
            "UPDATE learning_lessons SET content = $2 WHERE lesson_id = $1", lesson_id, content
        )
        # A lesson marked done straight from the path tree had no checkpoint
        # to turn into a review card yet -- now it does. sync is a no-op if
        # the lesson isn't complete or the card already exists.
        if row["completed_at"] is not None:
            try:
                await sync_lesson_review_card(pool, user_id, lesson_id, completed=True)
            except Exception:
                logger.warning(
                    "Lesson review-card backfill failed for lesson_id=%s", lesson_id, exc_info=True
                )
    elif isinstance(content, str):
        # Defensive only: the jsonb type codec (app/core/db.py) round-trips
        # this as a dict already. Kept in case a connection ever slips
        # through without it.
        content = json.loads(content)

    ordered_ids = await pool.fetch(
        """SELECT l.lesson_id
           FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE m.path_id = $1
           ORDER BY m.position, u.position, l.position""",
        path_id,
    )
    ids = [r["lesson_id"] for r in ordered_ids]
    index = ids.index(lesson_id)
    prev_id = ids[index - 1] if index > 0 else None
    next_id = ids[index + 1] if index < len(ids) - 1 else None

    return {
        "lesson_id": row["lesson_id"],
        "title": row["lesson_title"],
        "completed_at": row["completed_at"],
        "unit_title": row["unit_title"],
        "module_title": row["module_title"],
        "path_id": row["path_id"],
        "path_title": row["path_title"],
        "content": content,
        "prev_lesson_id": prev_id,
        "next_lesson_id": next_id,
        "review_card": await lesson_review_card(pool, lesson_id),
    }


def _lesson_card_topic(unit_title: str | None, module_title: str | None) -> str:
    """A lesson-derived card is tagged with its unit (falling back to its
    module) so it clusters with its siblings in the weak-topics analysis
    rather than showing up as one more 'untagged' entry."""
    return (unit_title or module_title or "").strip()


async def sync_lesson_review_card(
    pool: asyncpg.Pool, user_id: int, lesson_id: int, *, completed: bool
) -> None:
    """Keep a lesson's checkpoint in the spaced-repetition queue in step
    with whether the lesson is done.

    Completing: the checkpoint question becomes a free-text review card,
    scheduled by the same FSRS engine as every other practice format, so a
    finished path actually builds retention instead of just filling a
    progress bar. A fallback-content lesson (no LLM drafted its answer)
    still gets a card -- just an answerless, self-rated one, exactly like a
    hand-captured question with no answer.

    Uncompleting: the card is removed -- but only if it has never been
    reviewed. Once there's real attempt history the card is the student's,
    not the lesson's, and un-ticking a checkbox shouldn't delete practice
    they actually did.

    Best-effort by design: the caller runs this after the completion flip
    has already committed, so a failure here (a transient embed error, say)
    logs and leaves the lesson correctly marked done rather than 500ing the
    whole request. The next toggle re-syncs.
    """
    if not completed:
        await pool.execute(
            """DELETE FROM questions q
               WHERE q.source_lesson_id = $1
                 AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.question_id = q.question_id)""",
            lesson_id,
        )
        return

    row = await pool.fetchrow(
        """SELECT l.content, u.title AS unit_title, m.title AS module_title
           FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           WHERE l.lesson_id = $1""",
        lesson_id,
    )
    content = row["content"] if row else None
    if not content:
        # Lesson completed straight from the path tree without ever being
        # opened, so there's no generated checkpoint yet. get_lesson calls
        # this again once it generates content for an already-complete lesson.
        return

    question = (content.get("checkpoint_question") or "").strip()
    if not question:
        return
    answer = (content.get("checkpoint_answer") or "").strip()
    if answer == _LESSON_FALLBACK_ANSWER:
        answer = ""  # nothing real to grade against -> self-rate, same as any answerless card
    topic = _lesson_card_topic(row["unit_title"], row["module_title"])

    embedding = await embed_text_async(f"{question}\n{topic}".strip())
    await pool.execute(
        """INSERT INTO questions (user_id, question, answer, topic, source, embedding, source_lesson_id)
           VALUES ($1, $2, $3, $4, 'lesson', $5, $6)
           ON CONFLICT (source_lesson_id) WHERE source_lesson_id IS NOT NULL DO NOTHING""",
        user_id,
        question,
        answer,
        topic,
        embedding,
        lesson_id,
    )


async def lesson_review_card(pool: asyncpg.Pool, lesson_id: int) -> dict | None:
    """The review card a completed lesson produced, if any -- for the lesson
    page to show "in your review queue, due <date>" instead of leaving the
    connection between the two features invisible."""
    row = await pool.fetchrow(
        """SELECT q.question_id, cs.due::date AS due,
                  EXISTS (SELECT 1 FROM attempts a WHERE a.question_id = q.question_id) AS reviewed
           FROM questions q
           LEFT JOIN card_states cs ON cs.question_id = q.question_id
           WHERE q.source_lesson_id = $1""",
        lesson_id,
    )
    return dict(row) if row else None


async def toggle_lesson(pool: asyncpg.Pool, user_id: int, path_id: int, lesson_id: int) -> bool:
    """Ownership-checked (lesson -> unit -> module -> path -> user_id)
    flip of completed_at; returns the new completed state."""
    row = await pool.fetchrow(
        """UPDATE learning_lessons l
           SET completed_at = CASE WHEN l.completed_at IS NULL THEN now() ELSE NULL END
           FROM learning_units u, learning_modules m, learning_paths p
           WHERE l.lesson_id = $1
             AND l.unit_id = u.unit_id
             AND u.module_id = m.module_id
             AND m.path_id = p.path_id
             AND p.path_id = $2
             AND p.user_id = $3
           RETURNING l.completed_at""",
        lesson_id,
        path_id,
        user_id,
    )
    if row is None:
        raise PathNotFound(path_id)

    completed = row["completed_at"] is not None
    if completed:
        # Idempotent on lesson_id -- toggling a lesson off and back on
        # again re-completes it (real, useful behavior) without granting
        # XP a second time for the same lesson.
        await award_xp(pool, user_id, "lesson_complete", lesson_id, _XP_LESSON_COMPLETE)

    try:
        await sync_lesson_review_card(pool, user_id, lesson_id, completed=completed)
    except Exception:
        logger.warning("Lesson review-card sync failed for lesson_id=%s", lesson_id, exc_info=True)

    return completed
