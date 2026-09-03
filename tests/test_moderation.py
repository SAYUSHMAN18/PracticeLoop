"""Name moderation.

Scoped to the one place one user's text reaches another with no LLM in
between -- a classroom name, an assignment title. Fails open: no LLM
configured (the whole test suite's default) means every name passes, so
these tests opt the check in explicitly.
"""

from __future__ import annotations

import pytest

from app.core import moderation
from app.core.db import get_pool
from tests.conftest import signup


@pytest.fixture
def moderator(monkeypatch):
    """Turn the check on and stub the classifier. `verdict` maps a text
    substring to the label the fake model should return."""
    verdicts: dict[str, str] = {}

    monkeypatch.setattr(moderation, "is_configured", lambda: True)

    async def fake_generate(prompt: str, temperature: float = 0.0, **_: object) -> str:
        for needle, label in verdicts.items():
            if needle in prompt:
                return label
        return "OK"

    monkeypatch.setattr(moderation, "generate", fake_generate)
    return verdicts


async def test_check_passes_normal_text(moderator):
    assert (await moderation.check("Grade 9 Biology, Period 3")).ok


async def test_check_hard_blocks_abuse(moderator):
    moderator["SLUR_HERE"] = "ABUSE"
    result = await moderation.check("class name SLUR_HERE")
    assert result.category == "ABUSE"
    assert result.hard_block is True


async def test_check_reports_but_does_not_block_self_harm(moderator):
    moderator["hurt myself"] = "SELF_HARM"
    result = await moderation.check("I want to hurt myself")
    assert result.category == "SELF_HARM"
    assert result.hard_block is False


async def test_check_fails_open_on_an_unparseable_reply(monkeypatch):
    monkeypatch.setattr(moderation, "is_configured", lambda: True)

    async def junk(*_a, **_k):
        return "I'm not sure, maybe?"

    monkeypatch.setattr(moderation, "generate", junk)
    assert (await moderation.check("anything")).ok


async def test_check_fails_open_when_the_call_raises(monkeypatch):
    monkeypatch.setattr(moderation, "is_configured", lambda: True)

    async def boom(*_a, **_k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(moderation, "generate", boom)
    assert (await moderation.check("anything")).ok


async def test_check_is_a_noop_with_no_llm():
    # Default suite config: is_configured() is False.
    assert (await moderation.check("literally anything at all")).ok


# ---------- wired into classroom creation ----------


async def test_a_flagged_classroom_name_is_refused(client, monkeypatch):
    # classrooms/service.py imports `check` from app.core.moderation, and
    # check() reads these two module globals -- patch them there.
    async def flag_it(prompt, temperature=0.0, **_):
        return "ABUSE" if "BADNAME" in prompt else "OK"

    monkeypatch.setattr("app.core.moderation.is_configured", lambda: True)
    monkeypatch.setattr("app.core.moderation.generate", flag_it)

    await signup(client, "teacher-mod@example.com")
    await client.post("/profile/role", data={"role": "teacher"})

    bad = await client.post("/classrooms", data={"name": "BADNAME club"})
    assert bad.status_code == 400
    assert "was flagged" in bad.text

    ok = await client.post("/classrooms", data={"name": "Chemistry 101"}, follow_redirects=False)
    assert ok.status_code == 303

    pool = await get_pool()
    names = [r["name"] for r in await pool.fetch("SELECT name FROM classrooms")]
    assert names == ["Chemistry 101"]
