from io import BytesIO

from tests.conftest import signup


def _fake_pdf_bytes() -> bytes:
    return b"not actually a pdf, just renamed"


def _real_pdf_bytes() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _real_docx_bytes() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph("Fluent in Python and SQL.")
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --- Document vault upload (/documents) ---


async def test_documents_upload_rejects_a_fake_pdf(client):
    await signup(client, "upload-fakepdf-docs@example.com")
    files = {"file": ("resume.pdf", _fake_pdf_bytes(), "application/pdf")}
    response = await client.post("/documents", data={"doc_type": "resume"}, files=files)
    assert response.status_code == 400
    assert "look like a real PDF file" in response.text


async def test_documents_upload_rejects_a_fake_docx(client):
    await signup(client, "upload-fakedocx-docs@example.com")
    files = {
        "file": (
            "resume.docx",
            b"not a zip at all",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    }
    response = await client.post("/documents", data={"doc_type": "resume"}, files=files)
    assert response.status_code == 400
    assert "look like a real DOCX file" in response.text


async def test_documents_upload_rejects_a_text_file_containing_a_nul_byte(client):
    await signup(client, "upload-nul-docs@example.com")
    files = {"file": ("notes.txt", b"hello\x00world", "text/plain")}
    response = await client.post("/documents", data={"doc_type": "other"}, files=files)
    assert response.status_code == 400
    assert "look like plain text" in response.text


async def test_documents_upload_accepts_a_real_pdf(client):
    await signup(client, "upload-realpdf-docs@example.com")
    files = {"file": ("resume.pdf", _real_pdf_bytes(), "application/pdf")}
    response = await client.post("/documents", data={"doc_type": "resume"}, files=files)
    assert response.status_code == 200
    assert "resume.pdf" in response.text


async def test_documents_upload_accepts_a_real_docx(client):
    await signup(client, "upload-realdocx-docs@example.com")
    files = {
        "file": (
            "resume.docx",
            _real_docx_bytes(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    }
    response = await client.post("/documents", data={"doc_type": "resume"}, files=files)
    assert response.status_code == 200
    assert "resume.docx" in response.text


# --- Profile resume upload (/profile) ---


async def test_profile_resume_upload_rejects_a_fake_pdf(client):
    await signup(client, "upload-fakepdf-profile@example.com")
    files = {"resume": ("resume.pdf", _fake_pdf_bytes(), "application/pdf")}
    response = await client.post("/profile", data={"target_role": "", "target_companies": ""}, files=files)
    assert response.status_code == 400
    assert "read that file" in response.text


async def test_profile_resume_upload_accepts_a_real_docx(client):
    await signup(client, "upload-realdocx-profile@example.com")
    files = {
        "resume": (
            "resume.docx",
            _real_docx_bytes(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    }
    response = await client.post("/profile", data={"target_role": "", "target_companies": ""}, files=files)
    assert response.status_code == 200
