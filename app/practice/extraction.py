from __future__ import annotations

import json
import re

from app.core.llm import generate

_MARKER_PATTERN = re.compile(
    r"^(q|question|a|answer|example|topic|company|difficulty|code|language)\s*:\s*",
    re.IGNORECASE,
)

_FIELD_ALIASES = {
    "q": "question",
    "question": "question",
    "a": "answer",
    "answer": "answer",
    "example": "example",
    "topic": "topic",
    "company": "company",
    "difficulty": "difficulty",
    "code": "code_snippet",
    "language": "language",
}

_EMPTY_FIELDS = {
    "question": "",
    "answer": "",
    "example": "",
    "topic": "",
    "company": "",
    "difficulty": "medium",
    "code_snippet": "",
    "language": "",
}


def parse_markers(raw_text: str) -> dict:
    """Deterministic Q:/A:/Example:/... marker parsing -- works without any
    LLM key configured, same precedent CareerOS's documents/extraction.py set."""
    fields = dict(_EMPTY_FIELDS)
    current_key: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current_key is not None:
            fields[current_key] = "\n".join(buffer).strip()

    for line in raw_text.splitlines():
        match = _MARKER_PATTERN.match(line.strip())
        if match:
            flush()
            current_key = _FIELD_ALIASES[match.group(1).lower()]
            buffer = [line[match.end() :].strip()]
        elif current_key is not None:
            buffer.append(line)

    flush()
    return fields


_STRUCTURE_PROMPT = """Extract a single interview/practice question from the text below
into strict JSON with exactly these keys: question, answer, example, topic, company,
difficulty (one of "easy", "medium", "hard"), code_snippet, language.
Use "" for any field that isn't present in the text. Output ONLY the JSON object, no
markdown fences, no explanation.

TEXT:
{text}
"""


def _extract_first_json_object(text: str) -> str:
    """Find the first balanced {...} block, tolerant of a sentence or a
    markdown fence before/after it -- LLMs frequently ignore "output ONLY
    the JSON" and add either."""
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM response")

    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    raise ValueError("Unbalanced JSON object in LLM response")


def parse_llm_json_fields(response: str) -> dict:
    data = json.loads(_extract_first_json_object(response))
    fields = dict(_EMPTY_FIELDS)
    for key in fields:
        if data.get(key):
            fields[key] = str(data[key]).strip()
    return fields


async def structure_with_llm(raw_text: str) -> dict:
    prompt = _STRUCTURE_PROMPT.format(text=raw_text.strip())
    response = await generate(prompt, temperature=0.0)
    return parse_llm_json_fields(response)
