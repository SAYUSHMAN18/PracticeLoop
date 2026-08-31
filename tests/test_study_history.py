import re

from tests.conftest import signup


async def test_history_shows_nothing_reviewed_yet_when_empty(client):
    await signup(client, "history-empty@example.com")
    response = await client.get("/practice/history")
    assert response.status_code == 200
    assert "Nothing reviewed yet" in response.text


async def test_history_shows_a_recorded_attempt_grouped_by_day(client):
    await signup(client, "history@example.com")
    await client.post("/practice", data={"question": "History Q1", "answer": "A1", "topic": "history-topic"})
    bank = await client.get("/practice")
    question_id = re.search(r"/practice/(\d+)/edit", bank.text).group(1)

    await client.post(f"/practice/review/{question_id}", data={"rating": 4})

    response = await client.get("/practice/history")
    assert response.status_code == 200
    assert "History Q1" in response.text
    assert "history-topic" in response.text
    assert "4/5" in response.text
    assert "1 review" in response.text


async def test_history_is_isolated_per_user():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    client_a = AsyncClient(transport=transport, base_url="http://test")
    client_b = AsyncClient(transport=transport, base_url="http://test")
    await signup(client_a, "history-a@example.com")
    await signup(client_b, "history-b@example.com")

    await client_a.post("/practice", data={"question": "Only A sees this", "answer": "A", "topic": "t"})
    bank = await client_a.get("/practice")
    question_id = re.search(r"/practice/(\d+)/edit", bank.text).group(1)
    await client_a.post(f"/practice/review/{question_id}", data={"rating": 3})

    history_b = await client_b.get("/practice/history")
    assert "Only A sees this" not in history_b.text
    assert "Nothing reviewed yet" in history_b.text

    await client_a.aclose()
    await client_b.aclose()
