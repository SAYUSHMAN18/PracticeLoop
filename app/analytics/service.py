from __future__ import annotations

import asyncpg

from app.practice.fsrs_scheduler import retrievability_bulk

_TIMELINE_LIMIT = 50


async def get_retention_by_topic(pool: asyncpg.Pool, user_id: int) -> list[dict]:
    """FSRS's own current recall-probability estimate (already computed
    for the review queue, app/practice/fsrs_scheduler.py) averaged per
    topic -- "how likely are you to still remember this" made visible,
    instead of staying an internal number the review queue alone uses."""
    retrievability = await retrievability_bulk(pool, user_id)
    if not retrievability:
        return []

    rows = await pool.fetch(
        """SELECT question_id, coalesce(nullif(topic, ''), 'untagged') AS topic
           FROM questions WHERE user_id = $1""",
        user_id,
    )
    by_topic: dict[str, list[float]] = {}
    for row in rows:
        r = retrievability.get(row["question_id"])
        if r is not None:
            by_topic.setdefault(row["topic"], []).append(r)

    result = [
        {
            "topic": topic,
            "retention_percent": round(100 * sum(values) / len(values)),
            "question_count": len(values),
        }
        for topic, values in by_topic.items()
    ]
    result.sort(key=lambda r: r["retention_percent"])
    return result


async def get_timeline(pool: asyncpg.Pool, user_id: int, *, limit: int = _TIMELINE_LIMIT) -> list[dict]:
    """Merges four separate event sources into one chronological feed --
    real activity across the whole app, not just practice attempts."""
    attempts = await pool.fetch(
        """SELECT a.practiced_at AS at, q.question AS label, q.topic
           FROM attempts a JOIN questions q ON q.question_id = a.question_id
           WHERE a.user_id = $1 ORDER BY a.practiced_at DESC LIMIT $2""",
        user_id,
        limit,
    )
    lessons = await pool.fetch(
        """SELECT l.completed_at AS at, l.title AS label, p.title AS path_title
           FROM learning_lessons l
           JOIN learning_units u ON u.unit_id = l.unit_id
           JOIN learning_modules m ON m.module_id = u.module_id
           JOIN learning_paths p ON p.path_id = m.path_id
           WHERE p.user_id = $1 AND l.completed_at IS NOT NULL
           ORDER BY l.completed_at DESC LIMIT $2""",
        user_id,
        limit,
    )
    diagnostics = await pool.fetch(
        """SELECT created_at AS at, topic AS label, proficiency_result
           FROM diagnostic_attempts WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2""",
        user_id,
        limit,
    )
    projects = await pool.fetch(
        """SELECT submitted_at AS at, title AS label
           FROM projects WHERE user_id = $1 AND submitted_at IS NOT NULL
           ORDER BY submitted_at DESC LIMIT $2""",
        user_id,
        limit,
    )

    events = []
    for row in attempts:
        events.append(
            {
                "at": row["at"],
                "kind": "attempt",
                "text": f'Practiced "{row["label"]}"' + (f" ({row['topic']})" if row["topic"] else ""),
            }
        )
    for row in lessons:
        events.append(
            {
                "at": row["at"],
                "kind": "lesson",
                "text": f'Completed "{row["label"]}" in {row["path_title"]}',
            }
        )
    for row in diagnostics:
        events.append(
            {
                "at": row["at"],
                "kind": "diagnostic",
                "text": f'Diagnostic on "{row["label"]}" -- {row["proficiency_result"]}',
            }
        )
    for row in projects:
        events.append({"at": row["at"], "kind": "project", "text": f'Submitted project "{row["label"]}"'})

    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]


def get_recommendation(mastery: list[dict]) -> dict | None:
    """A deterministic, explainable pick -- no LLM call, just the real
    numbers already computed by dashboard/service.py's topic_mastery.
    Matches the plan's own example format: "X is recommended because
    your accuracy dropped to Y%" -- a real reason, not just a name."""
    if not mastery:
        return None
    weakest = mastery[0]  # topic_mastery() already sorts weakest-first
    return {
        "topic": weakest["topic"],
        "mastery_score": weakest["mastery_score"],
        "attempt_count": weakest["attempt_count"],
        "explanation": (
            f'"{weakest["topic"]}" is recommended because your mastery score there is '
            f"{weakest['mastery_score']}/100, based on {weakest['attempt_count']} "
            f"attempt{'s' if weakest['attempt_count'] != 1 else ''} -- your weakest tracked topic right now."
        ),
    }
