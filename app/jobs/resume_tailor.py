from __future__ import annotations

import json

from app.core.json_extraction import extract_first_json_value
from app.core.llm import generate
from app.core.logging import get_logger
from app.jobs.scoring import extract_keywords

logger = get_logger(__name__)

# Cap on how many emphasize/gap keywords the fallback surfaces -- past this
# it stops being a short, actionable list and starts being noise.
_MAX_FALLBACK_KEYWORDS = 20

_TAILOR_PROMPT = """You are helping a job candidate tailor their resume to one
specific job description, using ONLY what is genuinely true in the resume below.
Never invent employers, titles, dates, metrics, or skills that aren't already
in the resume -- if the JD wants something the resume doesn't show, that goes
in "gaps", not into a rewritten bullet.

RESUME:
{resume}

JOB DESCRIPTION:
{jd}

Produce a strict JSON object with this shape:
{{
  "summary": "a 2-3 sentence professional summary, tailored to this JD, from real resume content",
  "bullets": ["3 to 6 existing resume achievements rewritten to foreground what this JD wants"],
  "emphasize": ["skills already on the resume that this JD wants -- surface these more prominently"],
  "gaps": ["skills this JD wants that are NOT on the resume -- genuinely learn or add, never fabricate"]
}}
Output ONLY the JSON object. No markdown fences, no commentary.
"""


def _fallback_tailor(resume_text: str, jd_text: str) -> dict:
    """Deterministic fallback -- no LLM key required. A rewritten summary or
    bullets can't be produced responsibly without an LLM (there's no safe
    template for "rephrase this person's real achievements"), so instead of
    faking that, this surfaces the raw material: what the JD wants that the
    resume already has, and what it doesn't, using the same keyword-overlap
    tokenizer as the deterministic job-fit score.
    """
    jd_words = extract_keywords(jd_text)
    resume_words = extract_keywords(resume_text)
    emphasize = sorted(jd_words & resume_words)
    gaps = sorted(jd_words - resume_words)
    return {
        "ai_used": False,
        "summary": None,
        "bullets": [],
        "emphasize": emphasize[:_MAX_FALLBACK_KEYWORDS],
        "gaps": gaps[:_MAX_FALLBACK_KEYWORDS],
    }


async def tailor_resume(resume_text: str, jd_text: str, *, ai_available: bool) -> dict:
    """Diffs a resume against a JD and returns concrete, ready-to-use edits
    when an LLM is configured, or a keyword-overlap breakdown when it isn't
    -- same "always answer with *something* useful" principle as the rest of
    the LLM-backed features in this app, never a dead end just because a key
    isn't set or a call fails.
    """
    if not ai_available:
        return _fallback_tailor(resume_text, jd_text)

    try:
        response = await generate(
            _TAILOR_PROMPT.format(resume=resume_text.strip(), jd=jd_text.strip()), temperature=0.2
        )
        data = json.loads(extract_first_json_value(response))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return {
            "ai_used": True,
            "summary": (str(data.get("summary") or "").strip() or None),
            "bullets": [str(b).strip() for b in (data.get("bullets") or []) if str(b).strip()],
            "emphasize": [str(s).strip() for s in (data.get("emphasize") or []) if str(s).strip()],
            "gaps": [str(s).strip() for s in (data.get("gaps") or []) if str(s).strip()],
        }
    except Exception:
        logger.warning("Resume tailoring LLM call failed, falling back to keyword overlap", exc_info=True)
        return _fallback_tailor(resume_text, jd_text)
