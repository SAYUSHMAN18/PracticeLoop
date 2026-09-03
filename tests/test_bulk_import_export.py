"""Import a deck from Anki/Quizlet, export the bank as CSV.

The account's only export was JSON that nothing reads, and there was no
import at all. /practice/import takes the tab- or comma-separated text
both tools produce; /practice/export.csv writes plain CSV back.
"""

from __future__ import annotations

from app.practice.bulk_io import parse_bulk, to_csv
from tests.conftest import signup

# ---------- the parser ----------


def test_sniffs_tabs_the_way_anki_exports():
    rows = parse_bulk("mitochondria\tpowerhouse\nribosome\tprotein synthesis")
    assert rows == [
        {"question": "mitochondria", "answer": "powerhouse", "topic": ""},
        {"question": "ribosome", "answer": "protein synthesis", "topic": ""},
    ]


def test_skips_a_header_and_reads_a_topic_column():
    rows = parse_bulk("Question,Answer,Topic\nWhat is 2+2?,4,math")
    assert rows == [{"question": "What is 2+2?", "answer": "4", "topic": "math"}]


def test_drops_blank_lines_answerless_rows_and_in_batch_duplicates():
    rows = parse_bulk("a,1\n\nbareterm\na,2\nb,3")
    assert [r["question"] for r in rows] == ["a", "b"]
    assert rows[0]["answer"] == "1"  # first wins


def test_to_csv_round_trips():
    text = to_csv([{"question": "Q1", "answer": "A1", "topic": "t", "difficulty": "easy"}])
    assert text.splitlines()[0] == "question,answer,topic,difficulty"
    assert "Q1,A1,t,easy" in text


# ---------- the routes ----------


async def test_import_creates_questions(client):
    await signup(client, "importer@example.com")
    r = await client.post(
        "/practice/import",
        data={"pasted": "photosynthesis\tplants making food from light\nosmosis\twater across a membrane"},
    )
    assert r.status_code == 200
    assert "Imported 2" in r.text

    bank = await client.get("/practice")
    assert "photosynthesis" in bank.text
    assert "osmosis" in bank.text


async def test_import_dedupes_against_the_existing_bank(client):
    await signup(client, "dedupe-import@example.com")
    await client.post("/practice/import", data={"pasted": "term one\tdef one"})
    r = await client.post("/practice/import", data={"pasted": "term one\tdef one\nterm two\tdef two"})
    assert "Imported 1" in r.text  # "term one" already there


async def test_empty_import_is_a_friendly_error(client):
    await signup(client, "empty-import@example.com")
    r = await client.post("/practice/import", data={"pasted": "   "})
    assert r.status_code == 400
    assert "nothing" in r.text.lower() or "no rows" in r.text.lower()


async def test_export_csv_has_the_bank(client):
    await signup(client, "exporter@example.com")
    await client.post("/practice", data={"question": "Exported Q", "answer": "Exported A", "topic": "x"})
    r = await client.get("/practice/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "Exported Q,Exported A,x" in r.text
