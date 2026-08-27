from __future__ import annotations

from datetime import date, datetime, timezone

import asyncpg
import fsrs

# This app reviews on a day-by-day cadence ("come back tomorrow", "in a
# week", ...), never intra-session minute-level repeats -- FSRS's built-in
# (re)learning steps exist for Anki-style same-session drilling and would
# otherwise put every lapse or brand-new card straight back in today's
# queue a few minutes later instead of on the next real review day.
_SCHEDULER = fsrs.Scheduler(learning_steps=(), relearning_steps=())

# The app's self-rating and AI-grading scales are both 1-5 ("1 -- blackout"
# through "5 -- easy"); FSRS's Rating is 4-valued (Again/Hard/Good/Easy).
# 3 and 4 both collapse to Good -- a passing recall either way -- rather
# than inventing a fifth FSRS bucket that doesn't exist in the algorithm.
_RATING_MAP = {
    1: fsrs.Rating.Again,
    2: fsrs.Rating.Hard,
    3: fsrs.Rating.Good,
    4: fsrs.Rating.Good,
    5: fsrs.Rating.Easy,
}


def _card_from_row(row: asyncpg.Record | None) -> fsrs.Card:
    """A question with no card_states row, or one whose stability was
    never set, has no FSRS review history yet -- treat it as a brand new
    card rather than guessing at a memory state that doesn't exist."""
    if row is None or row["stability"] is None:
        return fsrs.Card()

    return fsrs.Card(
        state=fsrs.State(row["state"]),
        stability=row["stability"],
        difficulty=row["difficulty"],
        due=row["due"],
        last_review=row["last_review"],
    )


async def schedule_review(
    pool: asyncpg.Pool, user_id: int, question_id: int, rating: int, *, now: datetime | None = None
) -> date:
    """Runs one FSRS review for a question, persists its updated memory
    state, and returns the resulting due date -- a plain date, matching
    the return type the review-result templates already expect, so this
    is a drop-in replacement for the old next_review_date() call site."""
    if rating not in _RATING_MAP:
        raise ValueError(f"rating must be 1-5, got {rating}")

    now = now or datetime.now(timezone.utc)

    existing = await pool.fetchrow(
        "SELECT state, stability, difficulty, due, last_review FROM card_states WHERE question_id = $1",
        question_id,
    )
    card = _card_from_row(existing)
    updated_card, _log = _SCHEDULER.review_card(card, _RATING_MAP[rating], review_datetime=now)

    await pool.execute(
        """INSERT INTO card_states (question_id, user_id, state, stability, difficulty, due, last_review)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           ON CONFLICT (question_id) DO UPDATE SET
               state = EXCLUDED.state,
               stability = EXCLUDED.stability,
               difficulty = EXCLUDED.difficulty,
               due = EXCLUDED.due,
               last_review = EXCLUDED.last_review,
               updated_at = now()""",
        question_id,
        user_id,
        updated_card.state.value,
        updated_card.stability,
        updated_card.difficulty,
        updated_card.due,
        updated_card.last_review,
    )

    return updated_card.due.date()


async def retrievability(
    pool: asyncpg.Pool, question_id: int, *, now: datetime | None = None
) -> float | None:
    """Current recall-probability estimate (0-1) for a question, or None
    if it's never been reviewed. Not wired into the UI yet -- available
    for a future "how likely are you to still remember this" indicator."""
    row = await pool.fetchrow(
        "SELECT state, stability, difficulty, due, last_review FROM card_states WHERE question_id = $1",
        question_id,
    )
    if row is None or row["stability"] is None:
        return None
    card = _card_from_row(row)
    return _SCHEDULER.get_card_retrievability(card, now or datetime.now(timezone.utc))
