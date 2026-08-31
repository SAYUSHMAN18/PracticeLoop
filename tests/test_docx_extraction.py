from io import BytesIO

from app.profile.service import extract_text_from_file
from tests.conftest import signup


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_text_from_file_reads_a_real_docx():
    content = _make_docx_bytes(["First paragraph.", "Second paragraph with detail."])
    text = extract_text_from_file("notes.docx", content)
    assert "First paragraph." in text
    assert "Second paragraph with detail." in text


def test_extract_text_from_file_still_handles_plain_text():
    text = extract_text_from_file("notes.txt", b"Just plain text.")
    assert text == "Just plain text."


async def test_docx_resume_upload_extracts_text_through_the_document_vault(client):
    await signup(client, "docx-vault@example.com")
    docx_bytes = _make_docx_bytes(["Experienced backend engineer.", "Skilled in Python and SQL."])

    files = {
        "file": (
            "resume.docx",
            docx_bytes,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    }
    upload = await client.post("/documents", data={"doc_type": "resume"}, files=files)
    assert upload.status_code == 200

    profile = await client.get("/profile")
    assert "characters" in profile.text
