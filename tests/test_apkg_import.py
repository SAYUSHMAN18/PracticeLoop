"""Import a real Anki .apkg (a zip containing a SQLite collection),
not just the plain-text export tested in test_bulk_import_export.py.

Builds a synthetic .apkg matching Anki's real schema closely enough for
parse_apkg to read -- both the uncompressed collection.anki21 that older
Anki versions (and "support older Anki versions" exports) produce, and
the zstd-compressed collection.anki21b that's been the default since
Anki 2.1.50, since those take different code paths in the parser.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import zipfile

import pytest
import zstandard

from app.practice import bulk_io
from tests.conftest import signup

_SCHEMA = """
CREATE TABLE col (id INTEGER PRIMARY KEY, decks TEXT NOT NULL);
CREATE TABLE notes (id INTEGER PRIMARY KEY, flds TEXT NOT NULL);
CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER NOT NULL, did INTEGER NOT NULL);
"""


def _build_collection_db(notes: list[tuple[int, str, int]], decks: dict[int, str]) -> bytes:
    """notes: (note_id, flds, deck_id) triples. Returns the raw SQLite
    file bytes -- Anki's real column set is much wider, but these three
    tables are the only ones parse_apkg reads."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO col (id, decks) VALUES (1, ?)",
        (json.dumps({str(k): {"name": v} for k, v in decks.items()}),),
    )
    for note_id, flds, deck_id in notes:
        conn.execute("INSERT INTO notes (id, flds) VALUES (?, ?)", (note_id, flds))
        conn.execute("INSERT INTO cards (id, nid, did) VALUES (?, ?, ?)", (note_id * 10, note_id, deck_id))
    conn.commit()
    return _serialize(conn)


def _serialize(conn: sqlite3.Connection) -> bytes:
    # sqlite3.Connection.serialize() needs Python 3.11+; this repo's floor
    # is 3.10, so dump through a real temp file instead -- exactly what
    # parse_apkg itself does on the way back in.
    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        disk = sqlite3.connect(path)
        conn.backup(disk)
        disk.close()
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.remove(path)


def _apkg_bytes(notes, decks, *, collection_filename: str, compress: bool) -> bytes:
    db_bytes = _build_collection_db(notes, decks)
    if compress:
        db_bytes = zstandard.ZstdCompressor().compress(db_bytes)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(collection_filename, db_bytes)
    return buf.getvalue()


# ---------- the parser ----------


def test_reads_notes_from_an_uncompressed_anki21_collection():
    apkg = _apkg_bytes(
        [(1, "mitochondria\x1fthe powerhouse of the cell", 1)],
        {1: "Biology"},
        collection_filename="collection.anki21",
        compress=False,
    )
    rows = bulk_io.parse_apkg(apkg)
    assert rows == [{"question": "mitochondria", "answer": "the powerhouse of the cell", "topic": "Biology"}]


def test_reads_notes_from_a_zstd_compressed_anki21b_collection():
    """The default export format since Anki 2.1.50 -- if this path breaks,
    the importer silently stops working for the majority of decks anyone
    exports today."""
    apkg = _apkg_bytes(
        [(1, "ribosome\x1fmakes proteins", 1)],
        {1: "Biology"},
        collection_filename="collection.anki21b",
        compress=True,
    )
    rows = bulk_io.parse_apkg(apkg)
    assert rows == [{"question": "ribosome", "answer": "makes proteins", "topic": "Biology"}]


def test_prefers_anki21b_over_anki21_when_both_are_present():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        newer = zstandard.ZstdCompressor().compress(
            _build_collection_db([(1, "newer\x1fcorrect", 1)], {1: "D"})
        )
        older = _build_collection_db([(1, "older\x1fstale", 1)], {1: "D"})
        z.writestr("collection.anki21b", newer)
        z.writestr("collection.anki21", older)

    rows = bulk_io.parse_apkg(buf.getvalue())
    assert rows == [{"question": "newer", "answer": "correct", "topic": "D"}]


def test_strips_html_cloze_and_sound_refs_to_plain_text():
    flds = "<b>What is</b> a <i>mutex</i>?<br>[sound:q.mp3]\x1fA lock &amp; a queue{{c1::(hidden)}}"
    apkg = _apkg_bytes([(1, flds, 1)], {1: "CS"}, collection_filename="collection.anki21", compress=False)
    rows = bulk_io.parse_apkg(apkg)
    assert rows == [{"question": "What is a mutex?", "answer": "A lock & a queue(hidden)", "topic": "CS"}]


def test_skips_notes_with_fewer_than_two_fields():
    apkg = _apkg_bytes(
        [(1, "no answer field at all", 1), (2, "q2\x1fa2", 1)],
        {1: "D"},
        collection_filename="collection.anki21",
        compress=False,
    )
    rows = bulk_io.parse_apkg(apkg)
    assert rows == [{"question": "q2", "answer": "a2", "topic": "D"}]


def test_missing_collection_file_raises_a_clear_error():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("media", "{}")

    with pytest.raises(bulk_io.ApkgParseError, match="No Anki collection"):
        bulk_io.parse_apkg(buf.getvalue())


def test_not_a_zip_raises_a_clear_error():
    with pytest.raises(bulk_io.ApkgParseError, match="Not a valid .apkg"):
        bulk_io.parse_apkg(b"definitely not a zip")


# ---------- the route ----------


async def test_uploading_an_apkg_imports_its_notes(client):
    await signup(client, "apkg-importer@example.com")
    apkg = _apkg_bytes(
        [(1, "photosynthesis\x1fplants making food from light", 1)],
        {1: "Biology"},
        collection_filename="collection.anki21",
        compress=False,
    )
    response = await client.post(
        "/practice/import", files={"file": ("deck.apkg", apkg, "application/octet-stream")}
    )
    assert response.status_code == 200
    assert "Imported 1" in response.text

    bank = await client.get("/practice")
    assert "photosynthesis" in bank.text


async def test_a_corrupt_apkg_is_a_400_not_a_500(client):
    await signup(client, "apkg-corrupt@example.com")
    response = await client.post(
        "/practice/import", files={"file": ("deck.apkg", b"not a zip", "application/octet-stream")}
    )
    assert response.status_code == 400
    assert "Not a valid .apkg" in response.text
