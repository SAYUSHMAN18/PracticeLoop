from app.core.db import get_pool
from app.notifications import service
from tests.conftest import signup


async def test_assignment_created_notifies_classroom_members(client):
    from app.auth.service import create_user
    from app.classrooms.service import create_assignment, create_classroom

    pool = await get_pool()
    teacher_id = await create_user(pool, "notif-teacher@example.com", "testpassword123", "T")
    created = await create_classroom(pool, teacher_id, "Notif Class")

    await signup(client, "notif-student@example.com")
    await client.post("/classrooms/join", data={"join_code": created["join_code"]})
    student_id = await pool.fetchval(
        "SELECT user_id FROM users WHERE email = $1", "notif-student@example.com"
    )

    assert await service.unread_count(pool, student_id) == 0
    await create_assignment(pool, teacher_id, created["classroom_id"], "Read chapter 2", "", "", None)
    assert await service.unread_count(pool, student_id) == 1

    notifications = await service.list_notifications(pool, student_id)
    assert "Read chapter 2" in notifications[0]["title"]


async def test_guardian_accepted_notifies_the_student():
    from app.auth.service import create_user
    from app.guardian.service import accept_invite, create_invite

    pool = await get_pool()
    student_id = await create_user(pool, "notif-guardian-student@example.com", "testpassword123", "S")
    guardian_id = await create_user(pool, "notif-guardian-guardian@example.com", "testpassword123", "G")

    invite = await create_invite(pool, student_id)
    await accept_invite(pool, guardian_id, invite["invite_token"])

    assert await service.unread_count(pool, student_id) == 1


async def test_marking_one_read_redirects_to_its_link(client):
    await signup(client, "notif-markread@example.com")
    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "notif-markread@example.com")
    notification_id = await service.create(
        pool, user_id, "assignment_created", "Test notif", link="/classrooms"
    )

    response = await client.post(f"/notifications/{notification_id}/read", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/classrooms"
    assert await service.unread_count(pool, user_id) == 0


async def test_marking_an_already_read_notification_is_a_harmless_noop(client):
    await signup(client, "notif-doubleread@example.com")
    pool = await get_pool()
    user_id = await pool.fetchval(
        "SELECT user_id FROM users WHERE email = $1", "notif-doubleread@example.com"
    )
    notification_id = await service.create(pool, user_id, "assignment_created", "Test notif")

    await client.post(f"/notifications/{notification_id}/read")
    response = await client.post(f"/notifications/{notification_id}/read")
    assert response.status_code == 303


async def test_mark_all_read_clears_the_unread_count(client):
    await signup(client, "notif-markall@example.com")
    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "notif-markall@example.com")
    await service.create(pool, user_id, "assignment_created", "One")
    await service.create(pool, user_id, "assignment_created", "Two")
    assert await service.unread_count(pool, user_id) == 2

    response = await client.post("/notifications/read-all")
    assert response.status_code == 200
    assert await service.unread_count(pool, user_id) == 0


async def test_a_user_cannot_mark_another_users_notification_read(client):
    from app.auth.service import create_user

    pool = await get_pool()
    victim_id = await create_user(pool, "notif-victim@example.com", "testpassword123", "V")
    notification_id = await service.create(pool, victim_id, "assignment_created", "Private")

    await signup(client, "notif-attacker@example.com")
    response = await client.post(f"/notifications/{notification_id}/read")
    assert response.status_code == 404


async def test_topbar_shows_the_unread_badge_only_when_something_is_unread(client):
    await signup(client, "notif-badge@example.com")
    before = await client.get("/dashboard")
    assert "topbar-badge" not in before.text

    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "notif-badge@example.com")
    await service.create(pool, user_id, "assignment_created", "New thing")

    after = await client.get("/dashboard")
    assert "topbar-badge" in after.text
