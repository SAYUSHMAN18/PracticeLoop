"""Import and export the question bank as delimited text.

The account had a JSON export that nothing else can read, and no way to
bring a deck in from Anki or Quizlet. This is the bridge both directions
use the format those tools actually speak:

  * Anki: "Notes in Plain Text" is tab-separated; it imports CSV/TSV.
  * Quizlet: "Export" gives you term/definition separated by a chosen
    character (tab or comma), one card per line.

So the importer sniffs tab-vs-comma, takes column 1 as the question and
column 2 as the answer, and an optional column 3 as the topic. The
exporter writes plain CSV. No .apkg (that needs a zip + a versioned
SQLite schema) -- a follow-up if anyone asks.
"""

from __future__ import annotations

import csv
import io

# Ceiling per import. Each created question runs an embedding, so a
# multi-thousand-row paste would tie up the request past a proxy timeout;
# a bigger deck imports in a few passes.
_MAX_ROWS = 500
_HEADER_WORDS = {"question", "term", "front", "prompt", "answer", "definition", "back", "topic", "tags"}


def _looks_like_header(row: list[str]) -> bool:
    cells = [c.strip().lower() for c in row[:3]]
    return sum(1 for c in cells if c in _HEADER_WORDS) >= 2


def parse_bulk(text: str) -> list[dict]:
    """Delimited text -> a list of {question, answer, topic} dicts, ready
    for create_question. Blank lines and a leading header row are skipped;
    a row with no answer is dropped (a bare term isn't a reviewable card);
    duplicates within the batch (same question text) collapse to the
    first."""
    text = text.strip()
    if not text:
        return []

    sample = "\n".join(text.splitlines()[:20])
    delimiter = "\t" if sample.count("\t") >= sample.count(",") and "\t" in sample else ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    out: list[dict] = []
    seen: set[str] = set()
    for i, row in enumerate(reader):
        if not row or not any(cell.strip() for cell in row):
            continue
        if i == 0 and _looks_like_header(row):
            continue
        question = row[0].strip()
        answer = row[1].strip() if len(row) > 1 else ""
        topic = row[2].strip() if len(row) > 2 else ""
        if not question or not answer:
            continue
        key = question.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({"question": question, "answer": answer, "topic": topic})
        if len(out) >= _MAX_ROWS:
            break
    return out


def to_csv(rows: list) -> str:
    """The bank as CSV: question, answer, topic, difficulty. `rows` is any
    iterable indexable by those keys -- asyncpg Records from
    list_questions have all four."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["question", "answer", "topic", "difficulty"])
    for r in rows:
        writer.writerow([r["question"], r["answer"], r["topic"], r["difficulty"]])
    return buf.getvalue()
