from tests.conftest import signup


async def test_manual_question_create_edit_delete_roundtrip(client):
    await signup(client, "roundtrip@example.com")

    create = await client.post(
        "/practice",
        data={"question": "What is a linked list?", "answer": "A chain of nodes.", "topic": "ds"},
    )
    assert create.status_code == 303

    bank = await client.get("/practice")
    assert "What is a linked list?" in bank.text

    import re

    match = re.search(r"/practice/(\d+)/edit", bank.text)
    assert match, "expected an edit link for the created question"
    question_id = match.group(1)

    edit = await client.post(
        f"/practice/{question_id}/edit",
        data={
            "question": "What is a doubly linked list?",
            "answer": "A chain of nodes, both ways.",
            "topic": "ds",
        },
    )
    assert edit.status_code == 200
    assert "Saved" in edit.text
    assert "doubly linked list" in edit.text

    delete = await client.post(f"/practice/{question_id}/delete")
    assert delete.status_code == 303

    bank_after = await client.get("/practice")
    assert "doubly linked list" not in bank_after.text


async def test_edit_nonexistent_question_is_404(client):
    await signup(client, "editnotfound@example.com")
    response = await client.get("/practice/999999/edit")
    assert response.status_code == 404


async def test_rating_outside_1_to_5_is_rejected_not_500(client):
    await signup(client, "badrating@example.com")
    create = await client.post("/practice", data={"question": "Q", "answer": "A", "topic": "t"})
    assert create.status_code == 303

    bank = await client.get("/practice")
    import re

    question_id = re.search(r"/practice/(\d+)/edit", bank.text).group(1)

    too_high = await client.post(f"/practice/review/{question_id}", data={"rating": 6})
    assert too_high.status_code == 422

    too_low = await client.post(f"/practice/review/{question_id}", data={"rating": 0})
    assert too_low.status_code == 422


async def test_search_finds_semantically_similar_question(client):
    await signup(client, "search@example.com")
    await client.post(
        "/practice",
        data={"question": "What is a binary search tree?", "answer": "A sorted binary tree.", "topic": "ds"},
    )
    response = await client.get("/practice/search", params={"q": "tree data structure for fast lookup"})
    assert response.status_code == 200
    assert "binary search tree" in response.text


async def test_search_with_unrelated_query_shows_no_match(client):
    await signup(client, "nomatch@example.com")
    await client.post(
        "/practice",
        data={"question": "What is a binary search tree?", "answer": "A sorted binary tree.", "topic": "ds"},
    )
    response = await client.get("/practice/search", params={"q": "how do I bake sourdough bread"})
    assert response.status_code == 200
    assert "binary search tree" not in response.text


async def test_search_with_no_match_escapes_the_query_in_the_generate_link(client):
    """Regression test: the "no match, generate a study card?" link used to
    interpolate the raw query into an inline onclick handler, so a query
    containing a double quote broke the attribute (and the link) --
    and would have let a query string inject arbitrary onclick JS.
    Now the query goes into a plain data-* attribute (auto-escaped like
    any other Jinja text) and the handler itself contains no interpolation."""
    await signup(client, "search-escape@example.com")
    response = await client.get("/practice/search", params={"q": 'test" onmouseover="alert(1)'})
    assert response.status_code == 200
    assert 'onmouseover="alert' not in response.text
    assert "&#34;" in response.text or "&#39;" in response.text or "&quot;" in response.text
    assert 'data-topic="test&#34; onmouseover=&#34;alert(1)"' in response.text


async def test_review_flow_advances_queue_and_records_attempt(client):
    await signup(client, "review@example.com")
    await client.post("/practice", data={"question": "Q1", "answer": "A1", "topic": "t"})
    await client.post("/practice", data={"question": "Q2", "answer": "A2", "topic": "t"})

    queue = await client.get("/practice/review")
    assert "2 remaining" in queue.text

    import re

    bank = await client.get("/practice")
    first_id = re.search(r"/practice/(\d+)/edit", bank.text).group(1)

    rated = await client.post(f"/practice/review/{first_id}", data={"rating": 4})
    assert rated.status_code == 200
    assert "days" in rated.text.lower() or "review" in rated.text.lower()

    next_card = await client.get("/practice/review/next")
    assert "1 remaining" in next_card.text


async def test_profile_get_and_save_roundtrip(client):
    await signup(client, "profile@example.com")

    page = await client.get("/profile")
    assert page.status_code == 200

    saved = await client.post(
        "/profile", data={"target_role": "Backend Engineer", "target_companies": "Stripe"}
    )
    assert saved.status_code == 200
    assert "Saved" in saved.text
    assert "Backend Engineer" in saved.text


async def test_profile_resume_text_upload_is_extracted(client):
    await signup(client, "resume@example.com")

    files = {"resume": ("resume.txt", b"Experienced backend engineer with Python.", "text/plain")}
    response = await client.post("/profile", data={"target_role": "", "target_companies": ""}, files=files)
    assert response.status_code == 200
    assert "42 characters" in response.text or "characters" in response.text


async def test_profile_resume_over_size_cap_is_rejected(client):
    await signup(client, "bigresume@example.com")

    oversized = b"x" * (10 * 1024 * 1024 + 1)
    files = {"resume": ("resume.txt", oversized, "text/plain")}
    response = await client.post("/profile", data={"target_role": "", "target_companies": ""}, files=files)
    assert response.status_code == 413
