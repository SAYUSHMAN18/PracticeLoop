from httpx import ASGITransport, AsyncClient

from app.core.db import get_pool
from tests.conftest import signup


async def _two_logged_in_clients():
    from app.main import app

    transport = ASGITransport(app=app)
    client_a = AsyncClient(transport=transport, base_url="http://test")
    client_b = AsyncClient(transport=transport, base_url="http://test")
    await signup(client_a, "docs-victim@example.com")
    await signup(client_b, "docs-attacker@example.com")
    return client_a, client_b


async def test_upload_list_download_delete_roundtrip(client):
    await signup(client, "docs@example.com")

    files = {"file": ("transcript.txt", b"Fall 2025: GPA 3.9", "text/plain")}
    upload = await client.post(
        "/documents", data={"doc_type": "transcript", "title": "Fall transcript"}, files=files
    )
    assert upload.status_code == 200
    assert "Fall transcript" in upload.text
    assert "transcript.txt" in upload.text

    listing = await client.get("/documents")
    assert "Fall transcript" in listing.text

    import re

    match = re.search(r"/documents/(\d+)/download", listing.text)
    assert match, "expected a download link for the uploaded document"
    document_id = match.group(1)

    download = await client.get(f"/documents/{document_id}/download")
    assert download.status_code == 200
    assert download.content == b"Fall 2025: GPA 3.9"
    assert "attachment" in download.headers["content-disposition"]

    delete = await client.post(f"/documents/{document_id}/delete")
    assert delete.status_code == 303

    after = await client.get("/documents")
    assert "Fall transcript" not in after.text

    download_after_delete = await client.get(f"/documents/{document_id}/download")
    assert download_after_delete.status_code == 404


async def test_resume_tagged_upload_updates_profile_resume_text(client):
    await signup(client, "docs-resume@example.com")

    files = {"file": ("resume.txt", b"Experienced backend engineer with Python.", "text/plain")}
    upload = await client.post("/documents", data={"doc_type": "resume"}, files=files)
    assert upload.status_code == 200

    profile = await client.get("/profile")
    assert "42 characters" in profile.text or "characters" in profile.text

    dashboard = await client.get("/dashboard")
    assert "1 file in your vault" in dashboard.text


async def test_non_resume_upload_does_not_touch_profile_resume_text(client):
    await signup(client, "docs-cert@example.com")

    files = {"file": ("cert.txt", b"AWS Certified Solutions Architect", "text/plain")}
    upload = await client.post("/documents", data={"doc_type": "certificate"}, files=files)
    assert upload.status_code == 200

    profile = await client.get("/profile")
    assert "characters" not in profile.text


async def test_oversized_document_upload_is_rejected(client):
    await signup(client, "docs-big@example.com")

    oversized = b"x" * (10 * 1024 * 1024 + 1)
    files = {"file": ("huge.txt", oversized, "text/plain")}
    response = await client.post("/documents", data={"doc_type": "other"}, files=files)
    assert response.status_code == 413


async def test_unknown_doc_type_falls_back_to_other(client):
    await signup(client, "docs-badtype@example.com")

    files = {"file": ("mystery.txt", b"content", "text/plain")}
    response = await client.post("/documents", data={"doc_type": "not-a-real-type"}, files=files)
    assert response.status_code == 200
    assert "Other" in response.text


async def test_user_cannot_download_or_delete_another_users_document():
    client_a, client_b = await _two_logged_in_clients()

    files = {"file": ("secret.txt", b"victim's private notes", "text/plain")}
    upload = await client_a.post("/documents", data={"doc_type": "other"}, files=files)
    assert upload.status_code == 200

    pool = await get_pool()
    row = await pool.fetchrow("SELECT document_id FROM documents WHERE filename = 'secret.txt'")
    document_id = row["document_id"]

    download = await client_b.get(f"/documents/{document_id}/download")
    assert download.status_code == 404

    delete = await client_b.post(f"/documents/{document_id}/delete")
    assert delete.status_code == 404

    still_there = await pool.fetchrow("SELECT document_id FROM documents WHERE document_id = $1", document_id)
    assert still_there is not None, "attacker's delete should never have been applied"

    await client_a.aclose()
    await client_b.aclose()


async def test_document_title_with_quotes_is_safely_escaped_in_delete_form(client):
    """Regression-style guard: a title containing a double quote must not
    break out of the data-title attribute it's rendered into."""
    await signup(client, "docs-xss@example.com")

    files = {"file": ("weird.txt", b"content", "text/plain")}
    response = await client.post(
        "/documents",
        data={"doc_type": "other", "title": 'My "resume" v2'},
        files=files,
    )
    assert response.status_code == 200
    assert "&#34;" in response.text or "&quot;" in response.text
    assert (
        'data-title="My &#34;resume&#34; v2"' in response.text
        or 'data-title="My &quot;resume&quot; v2"' in response.text
    )
