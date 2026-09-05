from __future__ import annotations

import json
import random

import asyncpg

from app.core.json_extraction import extract_first_json_value
from app.core.llm import generate
from app.gamification.service import award_xp

# One LLM call still generates the whole item bank (same cost as the old
# fixed quiz), but administration is adaptive: DIFFICULTIES is a staircase
# (right answer -> harder, wrong answer -> easier), QUESTION_COUNT of the
# _PER_DIFFICULTY-per-tier pool actually get shown, chosen one at a time
# based on how the student is doing so far -- a real placement test, not
# a fixed-order quiz with three difficulty labels sprinkled through it.
DIFFICULTIES = ("easy", "medium", "hard")
_PER_DIFFICULTY = 5  # pool size = len(DIFFICULTIES) * _PER_DIFFICULTY = 15
QUESTION_COUNT = 8  # how many of the pool are actually administered
_MAX_CHOICES = 6
_XP_DIAGNOSTIC = 20  # a flat completion reward -- this is a placement test, not a "get it right" quiz

_DIAGNOSTIC_PROMPT = """Write a multiple-choice diagnostic item bank for this
topic: "{topic}"

Write exactly {per_difficulty} EASY, {per_difficulty} MEDIUM, and {per_difficulty} HARD
questions ({total} total). Each question should test a different subtopic within
"{topic}", so a wrong answer says something specific about what the student doesn't
know yet.

Output strict JSON only, no markdown fences, no commentary, in exactly this shape:
{{
  "questions": [
    {{
      "question": "...",
      "subtopic": "a short 2-4 word subtopic label",
      "difficulty": "easy",
      "choices": ["...", "...", "...", "..."],
      "correct_choice_index": 0
    }}
  ]
}}
"""


class DiagnosticUnavailable(Exception):
    """No LLM configured. Unlike a learning-path skeleton or lesson
    content, a diagnostic has no honest deterministic fallback -- there's
    no template for "generate real assessment questions on an arbitrary
    topic" that isn't just faking the assessment. Callers show a clear
    "needs an AI provider" message instead of a fake quiz."""


class DiagnosticGenerationFailed(Exception):
    """The LLM call happened but produced something unusable (bad JSON,
    zero valid questions after validation)."""


def _shuffle_choices(choices: list[str], correct_index: int, rng: random.Random) -> tuple[list[str], int]:
    """Randomize a generated question's answer order, remapping the correct
    index to wherever that choice landed.

    Models anchor hard on the shape of the JSON example in the prompt, and
    that example has to show *some* value for correct_choice_index. Measured
    over 48 freshly generated questions, the correct answer came back at
    index 0 for 67% of them, index 3 for none at all, and for two topics it
    was index 0 for all eight questions -- so "always pick the first option"
    scored ~67% without reading anything, and the resulting proficiency
    level and weak-subtopic list were both meaningless. Shuffling here (not
    by asking the prompt to vary it, which is unenforceable) makes the
    position uniform by construction, at zero token cost.
    """
    order = list(range(len(choices)))
    rng.shuffle(order)
    return [choices[i] for i in order], order.index(correct_index)


def _validate_questions(data: dict, *, rng: random.Random | None = None) -> list[dict]:
    """No cap at QUESTION_COUNT here -- this validates the whole generated
    item bank (up to len(DIFFICULTIES) * _PER_DIFFICULTY questions); which
    _PER_DIFFICULTY of them get administered is the adaptive engine's job,
    not the validator's."""
    rng = rng or random.Random()
    if not isinstance(data, dict):
        raise ValueError("not an object")
    raw_questions = data.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValueError("no questions")

    cleaned = []
    for q in raw_questions[: len(DIFFICULTIES) * _PER_DIFFICULTY]:
        if not isinstance(q, dict):
            continue
        question_text = str(q.get("question") or "").strip()
        choices_raw = q.get("choices")
        choices = [
            str(c).strip() for c in (choices_raw if isinstance(choices_raw, list) else []) if str(c).strip()
        ]
        choices = choices[:_MAX_CHOICES]
        correct_index = q.get("correct_choice_index")
        if not question_text or len(choices) < 2:
            continue
        if not isinstance(correct_index, int) or not (0 <= correct_index < len(choices)):
            continue
        difficulty = str(q.get("difficulty") or "").strip().lower()
        if difficulty not in DIFFICULTIES:
            difficulty = "medium"  # a model that omits/garbles this still slots into the staircase
        choices, correct_index = _shuffle_choices(choices, correct_index, rng)
        cleaned.append(
            {
                "question": question_text,
                "subtopic": str(q.get("subtopic") or "").strip(),
                "difficulty": difficulty,
                "choices": choices,
                "correct_choice_index": correct_index,
            }
        )

    if not cleaned:
        raise ValueError("no usable questions after validation")
    return cleaned


async def generate_diagnostic(topic: str, *, ai_available: bool) -> list[dict]:
    """Returns the full item bank (up to 15 questions spread across the
    three difficulty tiers) for callers to administer adaptively --
    see next_question() / record_answer() below."""
    if not ai_available:
        raise DiagnosticUnavailable()

    try:
        prompt = _DIAGNOSTIC_PROMPT.format(
            topic=topic.strip(), per_difficulty=_PER_DIFFICULTY, total=len(DIFFICULTIES) * _PER_DIFFICULTY
        )
        # Keyed on the topic only -- shareable across students.
        response = await generate(prompt, temperature=0.5, cacheable=True)
        data = json.loads(extract_first_json_value(response))
        return _validate_questions(data)
    except DiagnosticUnavailable:
        raise
    except Exception as exc:
        raise DiagnosticGenerationFailed() from exc


# --- Adaptive administration -------------------------------------------
#
# The item bank is generated once (generate_diagnostic, one LLM call, same
# cost as the old fixed quiz); everything below is a plain staircase over
# that bank, run entirely in Python against session state -- no extra LLM
# calls, no new failure mode the LLM budget has to absorb. Right answer ->
# next question one tier harder; wrong answer -> one tier easier. That's
# the actual definition of an adaptive placement test: which question you
# see next depends on how you've done so far, not a fixed order with
# difficulty labels sprinkled through it.


def _tier(difficulty: str) -> int:
    return DIFFICULTIES.index(difficulty) if difficulty in DIFFICULTIES else 1


def next_tier(tier: int, was_correct: bool) -> int:
    step = 1 if was_correct else -1
    return max(0, min(len(DIFFICULTIES) - 1, tier + step))


def pick_next_question(pool: list[dict], asked_indices: list[int], tier: int) -> int | None:
    """The unused pool question closest to `tier`, preferring an exact
    match. Widens outward instead of stopping early just because one
    tier's five questions ran out first -- a student who's aced every
    "hard" question shouldn't have the diagnostic quit on them."""
    unused = [i for i in range(len(pool)) if i not in asked_indices]
    if not unused:
        return None
    for distance in range(len(DIFFICULTIES)):
        candidates = [i for i in unused if abs(_tier(pool[i]["difficulty"]) - tier) == distance]
        if candidates:
            return candidates[0]
    return unused[0]  # unreachable given the loop above covers every possible distance


def start_session(pool: list[dict]) -> dict:
    """Fresh adaptive-diagnostic state for a newly generated item bank.
    Starts at the middle tier: with nothing yet known about the student,
    every other starting point is a worse bet."""
    state = {"pool": pool, "asked_indices": [], "results": [], "tier": 1, "current_index": None}
    state["current_index"] = pick_next_question(pool, [], 1)
    return state


def current_question(state: dict) -> dict | None:
    """The question to show right now, answer key stripped -- callers
    must never hand `correct_choice_index` to the client."""
    index = state["current_index"]
    if index is None:
        return None
    q = state["pool"][index]
    return {"question": q["question"], "choices": q["choices"]}


def is_complete(state: dict) -> bool:
    return state["current_index"] is None


def answer_current_question(state: dict, selected_index: int) -> dict:
    """Grades the in-play question, advances the staircase, and lines up
    what's next (or ends the session) -- mutates `state` in place. Returns
    {correct, subtopic} for this one question, so a caller accumulating a
    running tally doesn't have to re-derive it from `state` afterward."""
    index = state["current_index"]
    q = state["pool"][index]
    correct = selected_index == q["correct_choice_index"]

    state["asked_indices"].append(index)
    state["results"].append(correct)
    state["tier"] = next_tier(state["tier"], correct)
    state["current_index"] = (
        None
        if len(state["asked_indices"]) >= QUESTION_COUNT
        else pick_next_question(state["pool"], state["asked_indices"], state["tier"])
    )
    return {"correct": correct, "subtopic": q["subtopic"]}


def summarize(state: dict) -> tuple[int, int, list[str]]:
    """(correct_count, total_count, weak_subtopics) for record_attempt,
    once is_complete(state) is true."""
    correct_count = sum(1 for ok in state["results"] if ok)
    weak_subtopics = [
        state["pool"][i]["subtopic"]
        for i, ok in zip(state["asked_indices"], state["results"], strict=True)
        if not ok and state["pool"][i]["subtopic"]
    ]
    return correct_count, len(state["results"]), weak_subtopics


# (score-fraction floor, resulting level) -- checked high to low, first
# match wins. Reuses the exact level keys profile/service.py's own
# PROFICIENCY_LABELS already defines, so a diagnostic result and a
# self-reported one render through the same labels.
_LEVEL_THRESHOLDS = [
    (0.85, "advanced"),
    (0.65, "intermediate"),
    (0.35, "some_experience"),
    (0.0, "beginner"),
]


def score_to_level(correct_count: int, total_count: int) -> str:
    if total_count <= 0:
        return "beginner"
    fraction = correct_count / total_count
    for floor, level in _LEVEL_THRESHOLDS:
        if fraction >= floor:
            return level
    return "beginner"  # unreachable (0.0 always matches), kept for clarity


async def record_attempt(
    pool: asyncpg.Pool,
    user_id: int,
    topic: str,
    correct_count: int,
    total_count: int,
    weak_subtopics: list[str],
) -> dict:
    """Persists the result and, in the same transaction, updates the
    profile's proficiency to this *measured* value -- marked
    'diagnostic' so the profile/dashboard can show it as measured, not
    the Phase 2.1 self-reported dropdown value it may be overwriting."""
    level = score_to_level(correct_count, total_count)

    async with pool.acquire() as conn:
        async with conn.transaction():
            attempt_id = await conn.fetchval(
                """INSERT INTO diagnostic_attempts
                       (user_id, topic, correct_count, total_count, proficiency_result, weak_subtopics)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   RETURNING attempt_id""",
                user_id,
                topic,
                correct_count,
                total_count,
                level,
                weak_subtopics,
            )
            await conn.execute(
                """UPDATE profiles SET
                       proficiency_level = $2, proficiency_source = 'diagnostic', updated_at = now()
                   WHERE user_id = $1""",
                user_id,
                level,
            )
            # award_xp takes anything with asyncpg's execute() -- a
            # Connection works the same as a Pool here, and doing it
            # inside this same transaction means the XP and the result
            # it's for are always persisted together.
            await award_xp(conn, user_id, "diagnostic", attempt_id, _XP_DIAGNOSTIC)

    return {"attempt_id": attempt_id, "proficiency_result": level}


async def list_attempts(pool: asyncpg.Pool, user_id: int) -> list[asyncpg.Record]:
    return await pool.fetch(
        """SELECT attempt_id, topic, correct_count, total_count,
                  proficiency_result, weak_subtopics, created_at
           FROM diagnostic_attempts WHERE user_id = $1 ORDER BY created_at DESC""",
        user_id,
    )


async def get_attempt(pool: asyncpg.Pool, user_id: int, attempt_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """SELECT attempt_id, topic, correct_count, total_count,
                  proficiency_result, weak_subtopics, created_at
           FROM diagnostic_attempts WHERE attempt_id = $1 AND user_id = $2""",
        attempt_id,
        user_id,
    )
