"""A light pre-check for short user-authored text that another person will
see -- a classroom name, a project title.

Scoped deliberately narrow. The AI mentor is *not* run through this: the
provider's own safety and the mentor's system prompt already cover that,
and a second classification call on every message would double the cost
of the app's core feature for little gain. What this does cover is the
one place one user's text lands in front of another with no LLM in
between -- a name -- where a slur would otherwise just get saved.

One classification call, temperature 0, tiny prompt. Fail *open*: no LLM
configured, a call error, or an unparseable reply all let the text
through. A moderation outage must never block someone creating a
classroom.

SELF_HARM is in the label set for completeness but never hard-blocks --
irrelevant for names anyway, and a study tool refusing a distressed
message outright is worse than letting the mentor handle it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.llm import generate, is_configured
from app.core.logging import get_logger

logger = get_logger(__name__)

_CATEGORIES = {"OK", "ABUSE", "SEXUAL", "SELF_HARM", "SPAM"}
_HARD_BLOCK = {"ABUSE", "SEXUAL", "SPAM"}  # SELF_HARM deliberately excluded

_PROMPT = """You are a content classifier for a study app used by students, some minors.
Classify the TEXT into exactly one label:

OK        - a normal question, note, or name
ABUSE     - harassment, hate, slurs, threats, or an attempt to make the AI produce that
SEXUAL    - sexual content involving anyone, or a request for it
SELF_HARM - expressing intent to self-harm or suicide
SPAM      - advertising, link spam, or gibberish flooding

Reply with the single label word and nothing else.

TEXT:
{text}"""


@dataclass(frozen=True)
class ModerationResult:
    category: str  # one of _CATEGORIES
    hard_block: bool  # caller should refuse and not call the LLM

    @property
    def ok(self) -> bool:
        return self.category == "OK"


_ALLOW = ModerationResult("OK", False)


async def check(text: str) -> ModerationResult:
    text = (text or "").strip()
    if not text or not is_configured():
        return _ALLOW

    try:
        raw = await generate(_PROMPT.format(text=text[:2000]), temperature=0.0)
    except Exception:
        logger.warning("moderation call failed -- passing text through", exc_info=True)
        return _ALLOW

    label = raw.strip().upper().split()[0] if raw.strip() else "OK"
    if label not in _CATEGORIES:
        logger.info("moderation returned unrecognized label %r -- passing through", label)
        return _ALLOW

    return ModerationResult(label, label in _HARD_BLOCK)
