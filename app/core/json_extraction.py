from __future__ import annotations

_OPENERS = {"{": "}", "[": "]"}


def extract_first_json_value(text: str) -> str:
    """Finds the first balanced {...} or [...] in text, tolerant of a
    sentence or a markdown fence before/after it -- LLMs frequently ignore
    "output ONLY the JSON" and add either. Shared by practice/extraction.py
    (a single object) and jobs/gap_analysis.py (an array of skills)."""
    start = None
    closer = None
    for i, ch in enumerate(text):
        if ch in _OPENERS:
            start = i
            closer = _OPENERS[ch]
            break

    if start is None:
        raise ValueError("No JSON object or array found in LLM response")

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
        elif ch == text[start]:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    raise ValueError("Unbalanced JSON value in LLM response")
