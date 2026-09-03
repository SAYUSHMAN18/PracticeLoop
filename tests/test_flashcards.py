import re

from app.core.db import get_pool
from app.documents import flashcards
from app.documents.service import create_document
from tests.conftest import signup


async def test_empty_source_text_produces_no_cards():
    pool = await get_pool()
    result = await flashcards.generate_flashcards_from_text(
        pool, user_id=1, source_text="   ", topic="t", ai_available=True
    )
    assert result == []


async def test_fallback_without_ai_creates_one_summary_card():
    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "flashcard-fallback@example.com", "testpassword123", "Test")

    ids = await flashcards.generate_flashcards_from_text(
        pool, user_id, "Some real material about binary search trees.", "BSTs", ai_available=False
    )
    assert len(ids) == 1

    row = await pool.fetchrow(
        "SELECT question, answer, topic, source FROM questions WHERE question_id = $1", ids[0]
    )
    assert "BSTs" in row["question"]
    assert "binary search trees" in row["answer"]
    assert row["topic"] == "BSTs"
    assert row["source"] == "ai_generated"


async def test_ai_path_creates_a_card_per_generated_item(monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return (
            '[{"question": "What is a hash map?", "answer": "A key-value structure."}, '
            '{"question": "What is Big O?", "answer": "Asymptotic complexity."}]'
        )

    monkeypatch.setattr(flashcards, "generate", fake_generate)

    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "flashcard-ai@example.com", "testpassword123", "Test")

    ids = await flashcards.generate_flashcards_from_text(
        pool, user_id, "Data structures notes.", "DS notes", ai_available=True
    )
    assert len(ids) == 2

    rows = await pool.fetch("SELECT question, topic FROM questions WHERE question_id = ANY($1::int[])", ids)
    questions = {r["question"] for r in rows}
    assert "What is a hash map?" in questions
    assert "What is Big O?" in questions
    assert all(r["topic"] == "DS notes" for r in rows)


async def test_ai_failure_falls_back_to_summary_card(monkeypatch):
    async def failing_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(flashcards, "generate", failing_generate)

    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "flashcard-fail@example.com", "testpassword123", "Test")

    ids = await flashcards.generate_flashcards_from_text(
        pool, user_id, "Some material.", "topic", ai_available=True
    )
    assert len(ids) == 1


async def test_generate_flashcards_endpoint_creates_questions_and_redirects(client):
    await signup(client, "flashcard-endpoint@example.com")

    files = {"file": ("notes.txt", b"Notes about recursion and base cases.", "text/plain")}
    upload = await client.post(
        "/documents", data={"doc_type": "other", "title": "Recursion notes"}, files=files
    )
    assert upload.status_code == 200

    document_id = re.search(r"/documents/(\d+)/download", upload.text).group(1)

    response = await client.post(f"/documents/{document_id}/generate-flashcards")
    assert response.status_code == 303
    assert response.headers["location"] == "/practice"

    bank = await client.get("/practice")
    assert "Recursion notes" in bank.text


async def test_generate_flashcards_404s_for_another_users_document():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    client_a = AsyncClient(transport=transport, base_url="http://test")
    client_b = AsyncClient(transport=transport, base_url="http://test")
    await signup(client_a, "flashcard-victim@example.com")
    await signup(client_b, "flashcard-attacker@example.com")

    files = {"file": ("secret.txt", b"Victim's private notes.", "text/plain")}
    upload = await client_a.post("/documents", data={"doc_type": "other"}, files=files)
    document_id = re.search(r"/documents/(\d+)/download", upload.text).group(1)

    response = await client_b.post(f"/documents/{document_id}/generate-flashcards")
    assert response.status_code == 404

    await client_a.aclose()
    await client_b.aclose()


async def test_generate_flashcards_on_a_document_with_no_text_returns_an_error(client):
    await signup(client, "flashcard-notext@example.com")
    pool = await get_pool()
    from app.auth.service import get_user_by_email

    user = await get_user_by_email(pool, "flashcard-notext@example.com")
    document_id = await create_document(
        pool,
        user["user_id"],
        doc_type="other",
        title="No text doc",
        filename="blank.txt",
        mime_type="text/plain",
        content_bytes=b"x",
        extracted_text="",
    )

    response = await client.post(f"/documents/{document_id}/generate-flashcards")
    assert response.status_code == 400
    assert "no readable text" in response.text
