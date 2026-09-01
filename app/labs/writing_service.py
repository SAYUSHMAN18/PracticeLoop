from __future__ import annotations

import json

from app.core.json_extraction import extract_first_json_value
from app.core.llm import generate
from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_TEXT_CHARS = 8000
_MAX_ITEMS = 5

KINDS = {
    "essay": "Essay",
    "cover_letter": "Cover letter",
    "short_answer": "Short answer",
}

_WRITING_PROMPT = """You are a writing coach giving feedback on a student's {kind}.

TEXT:
{text}

Output strict JSON only, no markdown fences, no commentary, in exactly this shape:
{{
  "clarity_score": 1-5,
  "structure_score": 1-5,
  "grammar_score": 1-5,
  "strengths": ["short point", "short point"],
  "improvements": ["short, actionable point", "short, actionable point"],
  "summary": "one or two sentence overall take"
}}
"""


class WritingFeedbackUnavailable(Exception):
    """No LLM configured. Unlike Math Lab, there's no honest deterministic
    fallback for "give feedback on this writing" -- same call as the
    Phase 9 diagnostic."""


class WritingFeedbackFailed(Exception):
    pass


def _clean_score(value, default=3) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return default
    return min(5, max(1, score))


def _clean_list(value: list | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:_MAX_ITEMS]


async def get_feedback(text: str, kind: str, *, ai_available: bool) -> dict:
    if not text.strip():
        raise WritingFeedbackFailed("Paste some writing first.")
    if kind not in KINDS:
        kind = "essay"
    if not ai_available:
        raise WritingFeedbackUnavailable()

    prompt = _WRITING_PROMPT.format(kind=KINDS[kind].lower(), text=text[:_MAX_TEXT_CHARS])
    try:
        response = await generate(prompt, temperature=0.4)
        data = json.loads(extract_first_json_value(response))
    except Exception as exc:
        logger.warning("Writing Lab feedback generation failed", exc_info=True)
        raise WritingFeedbackFailed("Couldn't generate feedback right now -- try again in a moment.") from exc

    return {
        "clarity_score": _clean_score(data.get("clarity_score")),
        "structure_score": _clean_score(data.get("structure_score")),
        "grammar_score": _clean_score(data.get("grammar_score")),
        "strengths": _clean_list(data.get("strengths")),
        "improvements": _clean_list(data.get("improvements")),
        "summary": str(data.get("summary") or "").strip(),
    }
