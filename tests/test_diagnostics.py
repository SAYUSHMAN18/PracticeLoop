import random
import re

from app.assessments import service
from app.core.db import get_pool
from app.profile.service import get_profile, update_profile
from tests.conftest import signup

_FAKE_QUESTIONS_JSON = """{
  "questions": [
    {"question": "2 + 2?", "subtopic": "arithmetic", "choices": ["3", "4", "5"], "correct_choice_index": 1},
    {"question": "3 + 3?", "subtopic": "arithmetic", "choices": ["6", "7", "8"], "correct_choice_index": 0},
    {"question": "10 / 2?", "subtopic": "division", "choices": ["4", "5", "6"], "correct_choice_index": 1}
  ]
}"""


def _attempt_id_from_redirect(response) -> str:
    match = re.search(r"/assessments/result/(\d+)", str(response.headers["location"]))
    assert match, response.headers["location"]
    return match.group(1)


def _no_shuffle(monkeypatch) -> None:
    """Pin the generated answer order for tests that assert fixed choice
    indices. Those tests are about scoring and weak-subtopic reporting, not
    about where the correct answer sits -- _shuffle_choices has its own
    tests below."""
    monkeypatch.setattr(service, "_shuffle_choices", lambda choices, index, rng: (choices, index))


def test_score_to_level_boundaries():
    assert service.score_to_level(0, 8) == "beginner"
    assert service.score_to_level(2, 8) == "beginner"  # 25%
    assert service.score_to_level(3, 8) == "some_experience"  # 37.5%
    assert service.score_to_level(5, 8) == "some_experience"  # 62.5% -- below the 65% intermediate floor
    assert service.score_to_level(6, 8) == "intermediate"  # 75%
    assert service.score_to_level(7, 8) == "advanced"  # 87.5%
    assert service.score_to_level(8, 8) == "advanced"
    assert service.score_to_level(0, 0) == "beginner"


async def test_index_shows_ai_unavailable_notice_without_a_configured_provider(client):
    """GROQ_API_KEY is unset in the test environment -- this is the real
    no-AI-configured state, not a mocked one."""
    await signup(client, "diag-noai@example.com")
    response = await client.get("/assessments")
    assert response.status_code == 200
    assert "need an AI provider configured" in response.text


async def test_starting_a_diagnostic_without_ai_configured_shows_an_error_not_a_fake_quiz(client):
    await signup(client, "diag-noai-start@example.com")
    response = await client.post("/assessments/start", data={"topic": "Python"})
    assert response.status_code == 503
    assert "need an AI provider configured" in response.text


async def test_empty_topic_is_rejected(client):
    await signup(client, "diag-empty@example.com")
    response = await client.post("/assessments/start", data={"topic": "  "})
    assert response.status_code == 400
    assert "what to test you on" in response.text


async def test_taking_a_diagnostic_end_to_end_with_a_perfect_score(client, monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return _FAKE_QUESTIONS_JSON

    monkeypatch.setattr(service, "generate", fake_generate)
    monkeypatch.setattr("app.assessments.router.llm_is_configured", lambda: True)
    _no_shuffle(monkeypatch)

    await signup(client, "diag-perfect@example.com")
    start = await client.post("/assessments/start", data={"topic": "Arithmetic"}, follow_redirects=False)
    assert start.status_code == 303
    assert start.headers["location"] == "/assessments/take"

    take_page = await client.get("/assessments/take")
    assert take_page.status_code == 200
    assert "2 + 2?" in take_page.text
    # Each question must get its own distinct radio-group name -- a Jinja
    # nested-loop bug (using the inner `loop.index0` for the outer
    # question index) would give every question the SAME name instead.
    assert 'name="answer_0"' in take_page.text
    assert 'name="answer_1"' in take_page.text
    assert 'name="answer_2"' in take_page.text
    assert "correct_choice_index" not in take_page.text  # the answer key must not leak to the client

    submit = await client.post(
        "/assessments/submit",
        data={"answer_0": "1", "answer_1": "0", "answer_2": "1"},
        follow_redirects=False,
    )
    assert submit.status_code == 303
    attempt_id = _attempt_id_from_redirect(submit)

    result = await client.get(f"/assessments/result/{attempt_id}")
    assert result.status_code == 200
    assert "3/3" in result.text
    assert "Advanced" in result.text


async def test_taking_a_diagnostic_with_a_wrong_answer_reports_the_weak_subtopic(client, monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return _FAKE_QUESTIONS_JSON

    monkeypatch.setattr(service, "generate", fake_generate)
    monkeypatch.setattr("app.assessments.router.llm_is_configured", lambda: True)
    _no_shuffle(monkeypatch)

    await signup(client, "diag-partial@example.com")
    await client.post("/assessments/start", data={"topic": "Arithmetic"})
    # get 2/3 right, missing the division question (index 2, correct is "1")
    submit = await client.post(
        "/assessments/submit",
        data={"answer_0": "1", "answer_1": "0", "answer_2": "0"},
        follow_redirects=False,
    )
    attempt_id = _attempt_id_from_redirect(submit)

    result = await client.get(f"/assessments/result/{attempt_id}")
    assert "2/3" in result.text
    assert "division" in result.text


async def test_diagnostic_result_updates_profile_proficiency_as_measured(client, monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return _FAKE_QUESTIONS_JSON

    monkeypatch.setattr(service, "generate", fake_generate)
    monkeypatch.setattr("app.assessments.router.llm_is_configured", lambda: True)
    _no_shuffle(monkeypatch)

    await signup(client, "diag-profile@example.com")
    await client.post("/assessments/start", data={"topic": "Arithmetic"})
    await client.post("/assessments/submit", data={"answer_0": "1", "answer_1": "0", "answer_2": "1"})

    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "diag-profile@example.com")
    profile = await get_profile(pool, user_id)
    assert profile["proficiency_level"] == "advanced"
    assert profile["proficiency_source"] == "diagnostic"

    # Saving the profile form afterward must not leave a self-reported
    # value mislabeled as "measured".
    await update_profile(pool, user_id, "Engineer", "", proficiency_level="beginner")
    profile_after = await get_profile(pool, user_id)
    assert profile_after["proficiency_source"] == "self_reported"


async def test_taking_without_starting_redirects_home(client):
    await signup(client, "diag-notarted@example.com")
    response = await client.get("/assessments/take", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/assessments"


async def test_submitting_without_starting_redirects_home(client):
    await signup(client, "diag-nosubmit@example.com")
    response = await client.post("/assessments/submit", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/assessments"


async def test_another_users_diagnostic_result_404s():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    owner = AsyncClient(transport=transport, base_url="http://test")
    attacker = AsyncClient(transport=transport, base_url="http://test")

    await signup(owner, "diag-victim@example.com")
    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "diag-victim@example.com")
    result = await service.record_attempt(pool, user_id, "Victim topic", 3, 3, [])

    await signup(attacker, "diag-attacker@example.com")
    response = await attacker.get(f"/assessments/result/{result['attempt_id']}")
    assert response.status_code == 404

    await owner.aclose()
    await attacker.aclose()


def test_shuffle_choices_keeps_the_correct_answer_correct():
    """Whatever order comes out, correct_choice_index must still point at
    the same text it pointed at going in -- a remap bug here would silently
    mark right answers wrong for every diagnostic."""
    choices = ["alpha", "beta", "gamma", "delta"]
    for seed in range(200):
        shuffled, index = service._shuffle_choices(choices, 2, random.Random(seed))
        assert sorted(shuffled) == sorted(choices)
        assert shuffled[index] == "gamma"


def test_shuffle_choices_spreads_the_correct_answer_across_every_position():
    """The whole point: a model that always emits index 0 must not produce
    a quiz whose answer is always first. Every position should be reachable
    and roughly equally likely."""
    counts = [0, 0, 0, 0]
    for seed in range(400):
        _, index = service._shuffle_choices(["a", "b", "c", "d"], 0, random.Random(seed))
        counts[index] += 1

    assert all(c > 0 for c in counts), counts
    # 400 draws over 4 slots: 100 expected each. A generous band still
    # fails loudly if the remap collapses onto one position.
    assert all(50 < c < 150 for c in counts), counts


def test_validated_questions_do_not_inherit_the_models_index_0_bias():
    """End to end through the real validator: every question generated with
    the correct answer at index 0 must not still be at index 0 afterward."""
    data = {
        "questions": [
            {
                "question": f"q{i}?",
                "subtopic": "s",
                "choices": ["right", "wrong1", "wrong2", "wrong3"],
                "correct_choice_index": 0,
            }
            for i in range(8)
        ]
    }
    cleaned = service._validate_questions(data, rng=random.Random(7))

    assert len(cleaned) == 8
    for q in cleaned:
        assert q["choices"][q["correct_choice_index"]] == "right"
    assert len({q["correct_choice_index"] for q in cleaned}) > 1
