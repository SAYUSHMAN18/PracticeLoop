import re

from tests.conftest import signup


async def _upload(client, filename: str, doc_type: str = "other", content: bytes = b"content"):
    files = {"file": (filename, content, "text/plain")}
    response = await client.post("/documents", data={"doc_type": doc_type}, files=files)
    assert response.status_code == 200
    match = re.search(r"/documents/(\d+)/download", response.text)
    return match.group(1)


async def test_favoriting_a_document_sorts_it_first(client):
    await signup(client, "docfav@example.com")
    older_id = await _upload(client, "older.txt")
    await _upload(client, "newer.txt")

    # newer.txt sorts first by created_at DESC before any favoriting
    page = await client.get("/documents")
    assert page.text.index("newer.txt") < page.text.index("older.txt")

    # favoriting the older, un-favored one should now pull it to the top
    await client.post(f"/documents/{older_id}/favorite")
    page_after = await client.get("/documents")
    assert page_after.text.index("older.txt") < page_after.text.index("newer.txt")


async def test_favorite_toggle_is_idempotent_both_ways(client):
    await signup(client, "docfav-toggle@example.com")
    document_id = await _upload(client, "toggle.txt")

    first = await client.post(f"/documents/{document_id}/favorite")
    assert first.status_code == 303
    page1 = await client.get("/documents")
    assert 'aria-label="Unfavorite toggle.txt"' in page1.text

    await client.post(f"/documents/{document_id}/favorite")
    page2 = await client.get("/documents")
    assert 'aria-label="Favorite toggle.txt"' in page2.text


async def test_favorite_404s_for_another_users_document():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    client_a = AsyncClient(transport=transport, base_url="http://test")
    client_b = AsyncClient(transport=transport, base_url="http://test")
    await signup(client_a, "docfav-victim@example.com")
    await signup(client_b, "docfav-attacker@example.com")

    document_id = await _upload(client_a, "victim.txt")

    response = await client_b.post(f"/documents/{document_id}/favorite")
    assert response.status_code == 404

    await client_a.aclose()
    await client_b.aclose()


async def test_doc_type_filter_shows_only_matching_documents(client):
    await signup(client, "docfilter@example.com")
    await _upload(client, "my-resume.txt", doc_type="resume")
    await _upload(client, "my-cert.txt", doc_type="certificate")

    resumes = await client.get("/documents?doc_type=resume")
    assert "my-resume.txt" in resumes.text
    assert "my-cert.txt" not in resumes.text


async def test_favorites_only_filter_shows_only_favorited_documents(client):
    await signup(client, "docfilter-fav@example.com")
    fav_id = await _upload(client, "favorited.txt")
    await _upload(client, "not-favorited.txt")

    await client.post(f"/documents/{fav_id}/favorite")

    favorites_page = await client.get("/documents?favorites=1")
    assert "favorited.txt" in favorites_page.text
    assert "not-favorited.txt" not in favorites_page.text


async def test_invalid_doc_type_filter_is_ignored_not_500(client):
    await signup(client, "docfilter-bad@example.com")
    await _upload(client, "any.txt")

    response = await client.get("/documents?doc_type=not-a-real-type")
    assert response.status_code == 200
    assert "any.txt" in response.text
