import pytest

from app.labs import math_service
from tests.conftest import signup


def test_solves_a_simple_linear_equation():
    result = math_service.solve_equation("2x + 5 = 15")
    assert result["solutions"] == ["5"]


def test_solves_an_equation_with_x_on_both_sides():
    result = math_service.solve_equation("3x + 1 = x + 9")
    assert result["solutions"] == ["4"]


def test_rejects_disallowed_characters_before_ever_parsing():
    """The character whitelist is the first line of defense -- any
    letter other than "x" (and so any identifier like a module or
    builtin name) is rejected outright, before sympy's eval-based parser
    ever sees the string."""
    with pytest.raises(math_service.InvalidEquation, match="Only"):
        math_service.solve_equation("os.system(1) = 1")


def test_rejects_an_injection_attempt_disguised_as_an_equation():
    with pytest.raises(math_service.InvalidEquation):
        math_service.solve_equation("__import__('os') = 1")


def test_rejects_more_than_one_equals_sign():
    with pytest.raises(math_service.InvalidEquation, match="exactly one"):
        math_service.solve_equation("x = 1 = 2")


def test_rejects_unparseable_nonsense_gracefully():
    with pytest.raises(math_service.InvalidEquation):
        math_service.solve_equation("x + + = 5")


def test_rejects_an_equation_thats_too_long():
    with pytest.raises(math_service.InvalidEquation, match="too long"):
        math_service.solve_equation("x = " + "1+" * 150 + "1")


def test_rejects_empty_input():
    with pytest.raises(math_service.InvalidEquation):
        math_service.solve_equation("   ")


async def test_generate_steps_returns_none_without_ai():
    from app.auth.service import create_user
    from app.core.db import get_pool

    pool = await get_pool()
    user_id = await create_user(pool, "mathlab-noai@example.com", "testpassword123", "Test")
    steps = await math_service.generate_steps(pool, user_id, "2*x + 5 = 15", ["5"], ai_available=False)
    assert steps is None


async def test_math_lab_page_renders(client):
    await signup(client, "mathlab-page@example.com")
    response = await client.get("/labs/math")
    assert response.status_code == 200
    assert "Math Lab" in response.text


async def test_solving_an_equation_via_the_router_shows_the_answer(client):
    await signup(client, "mathlab-solve@example.com")
    response = await client.post("/labs/math/solve", data={"equation": "2x + 5 = 15"})
    assert response.status_code == 200
    assert "x = 5" in response.text


async def test_an_invalid_equation_via_the_router_shows_a_friendly_error_not_a_500(client):
    await signup(client, "mathlab-invalid@example.com")
    response = await client.post("/labs/math/solve", data={"equation": "not an equation"})
    assert response.status_code == 200
    assert "only" in response.text.lower()


async def test_steps_appear_when_ai_is_available(client, monkeypatch):
    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        return "Step 1: subtract 5.\nStep 2: divide by 2."

    monkeypatch.setattr(math_service, "generate", fake_generate)
    monkeypatch.setattr("app.labs.router.llm_is_configured", lambda: True)

    await signup(client, "mathlab-steps@example.com")
    response = await client.post("/labs/math/solve", data={"equation": "2x + 5 = 15"})
    assert response.status_code == 200
    assert "Step 1: subtract 5." in response.text
