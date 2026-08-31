from tests.conftest import signup


async def test_dashboard_shows_a_time_of_day_greeting_with_first_name(client):
    await signup(client, "greeting@example.com", name="Ada Lovelace")
    response = await client.get("/dashboard")
    assert response.status_code == 200
    assert "Ada" in response.text
    assert any(g in response.text for g in ("Good morning", "Good afternoon", "Good evening"))


async def test_greeting_falls_back_gracefully_for_an_invalid_timezone(client):
    await signup(client, "greeting-badtz@example.com")
    await client.post(
        "/profile",
        data={"target_role": "", "target_companies": "", "timezone": "Not/A/Real/Zone"},
    )
    response = await client.get("/dashboard")
    assert response.status_code == 200
    assert any(g in response.text for g in ("Good morning", "Good afternoon", "Good evening"))


async def test_dashboard_shows_a_7_day_activity_heatmap(client):
    await signup(client, "heatmap@example.com")
    response = await client.get("/dashboard")
    assert response.status_code == 200
    assert response.text.count("heatmap-day") == 7


async def test_dashboard_recommends_a_never_attempted_question_as_something_new(client):
    await signup(client, "newconcept@example.com")
    await client.post("/practice", data={"question": "What is a monad?", "answer": "...", "topic": "fp"})

    response = await client.get("/dashboard")
    assert "Something new" in response.text
    assert "What is a monad?" in response.text


async def test_dashboard_has_no_new_concept_card_once_everything_has_been_attempted(client):
    await signup(client, "noconcept@example.com")
    create = await client.post("/practice", data={"question": "Q1", "answer": "A1", "topic": "t"})
    assert create.status_code == 303

    import re

    bank = await client.get("/practice")
    question_id = re.search(r"/practice/(\d+)/edit", bank.text).group(1)
    await client.post(f"/practice/review/{question_id}", data={"rating": 3})

    response = await client.get("/dashboard")
    assert "Something new" not in response.text
