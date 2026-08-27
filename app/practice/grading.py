from __future__ import annotations

import json

from app.core.json_extraction import extract_first_json_value
from app.core.llm import generate

_GRADING_PROMPT = """Grade a student's answer to a practice question against the expected
answer below. Output strict JSON with exactly these keys: rating (an integer 1-5) and
feedback (a short string on what was missed or wrong, empty string if fully correct).

Rating scale: 1 = blackout/no relevant content, 2 = mostly wrong or missing key points,
3 = partially correct, missing important details, 4 = mostly correct with minor gaps,
5 = fully correct, matches the expected answer's key points.

Output ONLY the JSON object, no markdown fences, no explanation.

QUESTION: {question}

EXPECTED ANSWER: {expected_answer}

STUDENT'S ANSWER: {student_answer}
"""


async def grade_answer(question: str, expected_answer: str, student_answer: str) -> dict:
    """The single highest-value change to the review flow: a self-rating is
    unreliable input to a spaced-repetition scheduler (people rate
    themselves 5 on things they can't actually explain), so this replaces
    it with a graded typed answer wherever an LLM is configured to do the
    grading. Falls back to self-rating (see practice/router.py) when no
    LLM key is set, or when a question has no stored answer to grade
    against, or when this call itself fails -- a transient LLM hiccup
    shouldn't break the app's single most-used flow.
    """
    prompt = _GRADING_PROMPT.format(
        question=question,
        expected_answer=expected_answer,
        student_answer=student_answer.strip() or "(left blank)",
    )
    response = await generate(prompt, temperature=0.0)
    data = json.loads(extract_first_json_value(response))

    rating = max(1, min(5, int(data.get("rating", 3))))
    feedback = str(data.get("feedback", "")).strip()
    return {"rating": rating, "feedback": feedback}
