"""Import and export the question bank as delimited text, or a real Anki deck.

The account had a JSON export that nothing else can read, and no way to
bring a deck in from Anki or Quizlet. This is the bridge both directions
use the format those tools actually speak:

  * Anki: "Notes in Plain Text" is tab-separated; it imports CSV/TSV.
    A raw .apkg (its actual export format) is also accepted -- see
    parse_apkg below.
  * Quizlet: "Export" gives you term/definition separated by a chosen
    character (tab or comma), one card per line.

So the importer sniffs tab-vs-comma, takes column 1 as the question and
column 2 as the answer, and an optional column 3 as the topic. The
exporter writes plain CSV.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import zstandard

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


class ApkgParseError(Exception):
    """The upload wasn't a .apkg this parser could read -- not a zip, no
    recognizable collection file inside it, or a corrupt/undecodable
    database. Always a 400 to the user, never a 500."""


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_CLOZE_RE = re.compile(r"\{\{c\d+::(.*?)(?:::.*?)?\}\}")
_SOUND_REF_RE = re.compile(r"\[sound:[^\]]*\]")

# collection.anki21b (schema 18+, the default export since Anki 2.1.50) is
# zstd-compressed; collection.anki21 and the original collection.anki2 are
# the same SQLite schema family, uncompressed. Prefer the newest present --
# a deck exported with "support older Anki versions" on carries all three,
# and the older ones are historical copies for compatibility, not more
# complete than the newest.
_COLLECTION_FILES = ("collection.anki21b", "collection.anki21", "collection.anki2")


def _anki_field_to_text(raw: str) -> str:
    """Anki stores note fields as rich-text HTML -- collapse that to plain
    text rather than importing literal <div>/<br> tags that Jinja would
    then render escaped. There's no cloze card type here, so a cloze
    deletion ({{c1::answer}}) keeps just the hidden text."""
    text = _CLOZE_RE.sub(r"\1", raw)
    text = _SOUND_REF_RE.sub("", text)
    text = re.sub(r"<br\s*/?>|</p>|</div>", "\n", text, flags=re.IGNORECASE)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _deck_names_by_note_id(conn: sqlite3.Connection) -> dict[int, str]:
    """Best-effort note_id -> deck name, from the legacy JSON `col.decks`
    column every schema version still carries for backward compatibility.
    Never raises -- a collection where this doesn't parse imports with no
    topic rather than failing the whole upload over a nice-to-have."""
    try:
        decks_row = conn.execute("SELECT decks FROM col LIMIT 1").fetchone()
        decks = {str(k): v.get("name", "") for k, v in json.loads(decks_row[0]).items()}
        return {
            nid: decks.get(str(did), "")
            for nid, did in conn.execute("SELECT nid, did FROM cards")
            if decks.get(str(did))
        }
    except Exception:
        return {}


def parse_apkg(data: bytes) -> list[dict]:
    """A .apkg file (a zip containing a SQLite collection) -> the same
    {question, answer, topic} shape parse_bulk produces, ready for
    create_question.

    Reads every note's first two fields as question/answer -- covers
    Anki's stock Basic and Basic-(and-reversed-card) note types, which is
    the overwhelming majority of shared decks. A custom note type with a
    materially different field order still imports its first two fields
    verbatim rather than being silently skipped; cloze and image-occlusion
    types degrade to plain text since PracticeLoop has no equivalent card
    type for them."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ApkgParseError("Not a valid .apkg file (expected a zip archive).") from exc

    names = set(archive.namelist())
    collection_name = next((n for n in _COLLECTION_FILES if n in names), None)
    if collection_name is None:
        raise ApkgParseError("No Anki collection found inside this .apkg.")

    raw = archive.read(collection_name)
    if collection_name.endswith("b"):
        try:
            raw = zstandard.ZstdDecompressor().decompress(raw)
        except zstandard.ZstdError as exc:
            raise ApkgParseError("Couldn't decompress this .apkg's collection.") from exc

    with tempfile.TemporaryDirectory() as tmpdir:
        # sqlite3 needs a real file (or an in-memory serialize/deserialize
        # only available on Python 3.11+; this app's floor is 3.10) -- a
        # scratch file in its own throwaway directory, gone the moment
        # this block exits either way.
        db_path = Path(tmpdir) / "collection.sqlite"
        db_path.write_bytes(raw)
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT id, flds FROM notes").fetchall()
            deck_names = _deck_names_by_note_id(conn)
        except sqlite3.DatabaseError as exc:
            raise ApkgParseError("This .apkg's collection database looks corrupt.") from exc
        finally:
            conn.close()

    out: list[dict] = []
    seen: set[str] = set()
    for note_id, flds in rows:
        fields = (flds or "").split("\x1f")
        if len(fields) < 2:
            continue
        question = _anki_field_to_text(fields[0])
        answer = _anki_field_to_text(fields[1])
        if not question or not answer:
            continue
        key = question.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({"question": question, "answer": answer, "topic": deck_names.get(note_id, "")})
        if len(out) >= _MAX_ROWS:
            break
    return out
