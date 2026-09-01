import re

from app.core.db import get_pool
from app.guardian import service
from app.practice.service import create_question, record_attempt
from tests.conftest import signup


def _extract_token(index_page_text: str) -> str:
    match = re.search(r"/guardian/accept/([\w-]+)", index_page_text)
    assert match, "no invite link found on the page"
    return match.group(1)


async def test_creating_an_invite_shows_a_pending_link(client):
    await signup(client, "guardian-student@example.com")
    response = await client.post("/guardian/invite", follow_redirects=True)
    assert response.status_code == 200
    assert "Pending" in response.text
    assert "/guardian/accept/" in response.text


async def test_accepting_an_invite_links_the_guardian(client):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    student = AsyncClient(transport=transport, base_url="http://test")
    guardian = AsyncClient(transport=transport, base_url="http://test")

    await signup(student, "guardian-flow-student@example.com")
    invite_page = await student.post("/guardian/invite", follow_redirects=True)
    token = _extract_token(invite_page.text)

    await signup(guardian, "guardian-flow-guardian@example.com")
    accept_page = await guardian.get(f"/guardian/accept/{token}")
    assert accept_page.status_code == 200
    assert "guardian-flow-student" not in accept_page.text  # shows the student's *name*, not their email
    assert "Accept" in accept_page.text

    accept = await guardian.post(f"/guardian/accept/{token}", follow_redirects=False)
    assert accept.status_code == 303

    guardian_page = await guardian.get("/guardian")
    assert "Test" in guardian_page.text  # the default signup() display name

    student_page = await student.get("/guardian")
    assert "Accepted by Test" in student_page.text

    await student.aclose()
    await guardian.aclose()


async def test_guardian_view_shows_real_summary_stats():
    pool = await get_pool()
    from app.auth.service import create_user

    student_id = await create_user(pool, "guardian-stats-student@example.com", "testpassword123", "Kid")
    guardian_id = await create_user(pool, "guardian-stats-guardian@example.com", "testpassword123", "Parent")

    invite = await service.create_invite(pool, student_id)
    await service.accept_invite(pool, guardian_id, invite["invite_token"])

    q = await create_question(pool, student_id, {"question": "Q", "answer": "A", "topic": "t"})
    await record_attempt(pool, student_id, q, rating=5)

    students = await service.list_students_for_guardian(pool, guardian_id)
    assert len(students) == 1
    assert students[0]["name"] == "Kid"
    assert students[0]["total_xp"] == 10


async def test_a_student_cannot_accept_their_own_invite(client):
    await signup(client, "guardian-self@example.com")
    invite_page = await client.post("/guardian/invite", follow_redirects=True)
    token = _extract_token(invite_page.text)

    response = await client.post(f"/guardian/accept/{token}")
    assert response.status_code == 400


async def test_accepting_an_already_accepted_invite_404s():
    pool = await get_pool()
    from app.auth.service import create_user

    student_id = await create_user(pool, "guardian-reaccept-student@example.com", "testpassword123", "S")
    first_guardian = await create_user(pool, "guardian-reaccept-first@example.com", "testpassword123", "G1")
    second_guardian = await create_user(pool, "guardian-reaccept-second@example.com", "testpassword123", "G2")

    invite = await service.create_invite(pool, student_id)
    await service.accept_invite(pool, first_guardian, invite["invite_token"])

    try:
        await service.accept_invite(pool, second_guardian, invite["invite_token"])
        raise AssertionError("expected InviteNotFound")
    except service.InviteNotFound:
        pass


async def test_revoking_a_pending_invite_blocks_acceptance(client):
    await signup(client, "guardian-revoke-pending@example.com")
    invite_page = await client.post("/guardian/invite", follow_redirects=True)
    token = _extract_token(invite_page.text)

    pool = await get_pool()
    link_id = await pool.fetchval("SELECT link_id FROM guardian_links WHERE invite_token = $1", token)
    assert link_id is not None

    await client.post(f"/guardian/invite/{link_id}/revoke")

    accept_page = await client.get(f"/guardian/accept/{token}")
    assert "revoked" in accept_page.text.lower()


async def test_revoking_accepted_access_removes_the_guardian():
    pool = await get_pool()
    from app.auth.service import create_user

    student_id = await create_user(pool, "guardian-revoke-student@example.com", "testpassword123", "S")
    guardian_id = await create_user(pool, "guardian-revoke-guardian@example.com", "testpassword123", "G")
    invite = await service.create_invite(pool, student_id)
    await service.accept_invite(pool, guardian_id, invite["invite_token"])
    assert len(await service.list_students_for_guardian(pool, guardian_id)) == 1

    await service.revoke_invite(pool, student_id, invite["link_id"])
    assert len(await service.list_students_for_guardian(pool, guardian_id)) == 0


async def test_a_student_cannot_revoke_another_students_invite(client):
    pool = await get_pool()
    from app.auth.service import create_user

    victim_id = await create_user(pool, "guardian-revoke-victim@example.com", "testpassword123", "V")
    invite = await service.create_invite(pool, victim_id)

    await signup(client, "guardian-revoke-attacker@example.com")
    response = await client.post(f"/guardian/invite/{invite['link_id']}/revoke")
    assert response.status_code == 404


async def test_an_unknown_invite_token_404s(client):
    await signup(client, "guardian-unknown-token@example.com")
    response = await client.get("/guardian/accept/not-a-real-token")
    assert response.status_code == 404
