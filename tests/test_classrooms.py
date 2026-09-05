import re

from app.classrooms import service
from app.core.db import get_pool
from app.practice.service import create_question, record_attempt
from tests.conftest import signup


def _classroom_id_from_redirect(response) -> str:
    match = re.search(r"/classrooms/(\d+)", str(response.headers["location"]))
    assert match, response.headers["location"]
    return match.group(1)


async def _become_teacher(client) -> None:
    response = await client.post("/profile/role", data={"role": "teacher"})
    assert response.status_code == 303


async def test_invalid_role_is_rejected(client):
    await signup(client, "role-invalid@example.com")
    response = await client.post("/profile/role", data={"role": "admin"})
    assert response.status_code == 400


async def test_a_student_cannot_create_a_classroom(client):
    await signup(client, "classroom-student-only@example.com")
    response = await client.post("/classrooms", data={"name": "My Classroom"})
    assert response.status_code == 403


async def test_a_teacher_can_create_a_classroom_with_a_real_join_code(client):
    await signup(client, "classroom-teacher@example.com")
    await _become_teacher(client)

    response = await client.post("/classrooms", data={"name": "Period 3 Biology"}, follow_redirects=False)
    assert response.status_code == 303
    classroom_id = _classroom_id_from_redirect(response)

    pool = await get_pool()
    join_code = await pool.fetchval(
        "SELECT join_code FROM classrooms WHERE classroom_id = $1", int(classroom_id)
    )
    assert len(join_code) == 6

    detail = await client.get(f"/classrooms/{classroom_id}")
    assert detail.status_code == 200
    assert "Period 3 Biology" in detail.text
    assert join_code in detail.text


async def test_joining_with_an_invalid_code_shows_an_error(client):
    await signup(client, "classroom-badcode@example.com")
    response = await client.post("/classrooms/join", data={"join_code": "ZZZZZZ"})
    assert response.status_code == 400
    # avoids the apostrophe in "doesn't" -- Jinja escapes it to &#39;
    assert "match a classroom" in response.text


async def test_joining_is_idempotent(client):
    pool = await get_pool()
    from app.auth.service import create_user

    teacher_id = await create_user(pool, "classroom-owner2@example.com", "testpassword123", "Test")
    created = await service.create_classroom(pool, teacher_id, "History")

    await signup(client, "classroom-joiner@example.com")
    await client.post("/classrooms/join", data={"join_code": created["join_code"]})
    await client.post("/classrooms/join", data={"join_code": created["join_code"]})  # joining twice

    student_id = await pool.fetchval(
        "SELECT user_id FROM users WHERE email = $1", "classroom-joiner@example.com"
    )
    count = await pool.fetchval(
        "SELECT count(*) FROM classroom_members WHERE classroom_id = $1 AND student_user_id = $2",
        created["classroom_id"],
        student_id,
    )
    assert count == 1


async def test_roster_shows_real_student_activity(client):
    pool = await get_pool()
    from app.auth.service import create_user

    teacher_id = await create_user(pool, "classroom-roster-teacher@example.com", "testpassword123", "Test")
    created = await service.create_classroom(pool, teacher_id, "Chemistry")

    student_id = await create_user(pool, "classroom-roster-student@example.com", "testpassword123", "Student")
    await service.join_classroom(pool, student_id, created["join_code"])
    q = await create_question(pool, student_id, {"question": "Q", "answer": "A", "topic": "t"})
    await record_attempt(pool, student_id, q, rating=5)

    roster = await service.get_roster(pool, teacher_id, created["classroom_id"])
    assert len(roster) == 1
    assert roster[0]["name"] == "Student"
    assert roster[0]["total_xp"] == 10  # a rating-5 attempt is worth 10 XP


async def test_a_teacher_cannot_view_another_teachers_classroom(client):
    pool = await get_pool()
    from app.auth.service import create_user

    other_teacher_id = await create_user(pool, "classroom-other-teacher@example.com", "testpassword123", "T")
    created = await service.create_classroom(pool, other_teacher_id, "Someone else's class")

    await signup(client, "classroom-attacker-teacher@example.com")
    await _become_teacher(client)

    response = await client.get(f"/classrooms/{created['classroom_id']}")
    assert response.status_code == 404


async def test_a_teacher_cannot_add_an_assignment_to_another_teachers_classroom(client):
    pool = await get_pool()
    from app.auth.service import create_user

    other_teacher_id = await create_user(pool, "classroom-other-teacher2@example.com", "testpassword123", "T")
    created = await service.create_classroom(pool, other_teacher_id, "Someone else's class")

    await signup(client, "classroom-attacker-assign@example.com")
    await _become_teacher(client)

    response = await client.post(
        f"/classrooms/{created['classroom_id']}/assignments", data={"title": "Hijacked assignment"}
    )
    assert response.status_code == 404


async def test_a_teacher_cannot_delete_another_teachers_classroom(client):
    pool = await get_pool()
    from app.auth.service import create_user

    other_teacher_id = await create_user(pool, "classroom-other-teacher3@example.com", "testpassword123", "T")
    created = await service.create_classroom(pool, other_teacher_id, "Someone else's class")

    await signup(client, "classroom-attacker-delete@example.com")
    await _become_teacher(client)

    response = await client.post(f"/classrooms/{created['classroom_id']}/delete")
    assert response.status_code == 404

    still_there = await pool.fetchval(
        "SELECT classroom_id FROM classrooms WHERE classroom_id = $1", created["classroom_id"]
    )
    assert still_there is not None


async def test_a_student_only_sees_assignments_from_classrooms_theyve_joined(client):
    pool = await get_pool()
    from app.auth.service import create_user

    teacher_id = await create_user(pool, "classroom-assign-teacher@example.com", "testpassword123", "T")
    joined = await service.create_classroom(pool, teacher_id, "Joined class")
    not_joined = await service.create_classroom(pool, teacher_id, "Not joined class")
    await service.create_assignment(pool, teacher_id, joined["classroom_id"], "Read chapter 1", "", "", None)
    await service.create_assignment(
        pool, teacher_id, not_joined["classroom_id"], "Secret assignment", "", "", None
    )

    await signup(client, "classroom-assign-student@example.com")
    await client.post("/classrooms/join", data={"join_code": joined["join_code"]})

    page = await client.get("/classrooms")
    assert "Read chapter 1" in page.text
    assert "Secret assignment" not in page.text


async def test_a_student_can_view_a_classroom_theyve_joined(client):
    pool = await get_pool()
    from app.auth.service import create_user

    teacher_id = await create_user(pool, "classroom-student-view-teacher@example.com", "testpassword123", "T")
    created = await service.create_classroom(pool, teacher_id, "Geometry")

    await signup(client, "classroom-student-view@example.com")
    await client.post("/classrooms/join", data={"join_code": created["join_code"]})

    detail = await client.get(f"/classrooms/{created['classroom_id']}")
    assert detail.status_code == 200
    assert "Geometry" in detail.text
    assert "T" in detail.text  # the teacher's name
    # a student's own classroom page must never show the delete-classroom form
    assert "Delete classroom" not in detail.text


async def test_a_non_member_cannot_view_a_classroom(client):
    pool = await get_pool()
    from app.auth.service import create_user

    teacher_id = await create_user(pool, "classroom-nonmember-teacher@example.com", "testpassword123", "T")
    created = await service.create_classroom(pool, teacher_id, "Private class")

    await signup(client, "classroom-nonmember@example.com")
    response = await client.get(f"/classrooms/{created['classroom_id']}")
    assert response.status_code == 404


async def test_leaderboard_ranks_by_xp_and_excludes_opted_out_members(client):
    pool = await get_pool()
    from app.auth.service import create_user
    from app.profile.service import set_leaderboard_opt_out

    teacher_id = await create_user(pool, "classroom-lb-teacher@example.com", "testpassword123", "T")
    created = await service.create_classroom(pool, teacher_id, "Leaderboard class")

    top_id = await create_user(pool, "classroom-lb-top@example.com", "testpassword123", "Top Student")
    low_id = await create_user(pool, "classroom-lb-low@example.com", "testpassword123", "Low Student")
    hidden_id = await create_user(
        pool, "classroom-lb-hidden@example.com", "testpassword123", "Hidden Student"
    )
    for uid in (top_id, low_id, hidden_id):
        await service.join_classroom(pool, uid, created["join_code"])

    q_top = await create_question(pool, top_id, {"question": "Q", "answer": "A", "topic": "t"})
    await record_attempt(pool, top_id, q_top, rating=5)  # 10 XP
    q_low = await create_question(pool, low_id, {"question": "Q", "answer": "A", "topic": "t"})
    await record_attempt(pool, low_id, q_low, rating=1)  # less XP than a rating-5 attempt
    q_hidden = await create_question(pool, hidden_id, {"question": "Q", "answer": "A", "topic": "t"})
    await record_attempt(pool, hidden_id, q_hidden, rating=5)
    await set_leaderboard_opt_out(pool, hidden_id, True)

    leaderboard = await service.get_leaderboard(pool, created["classroom_id"])
    assert [row["name"] for row in leaderboard] == ["Top Student", "Low Student"]
    assert leaderboard[0]["total_xp"] >= leaderboard[1]["total_xp"]


async def test_assignment_progress_counts_attempts_since_it_was_assigned(client):
    pool = await get_pool()
    from app.auth.service import create_user

    teacher_id = await create_user(pool, "classroom-progress-teacher@example.com", "testpassword123", "T")
    created = await service.create_classroom(pool, teacher_id, "Progress class")
    student_id = await create_user(pool, "classroom-progress-student@example.com", "testpassword123", "Prog")
    await service.join_classroom(pool, student_id, created["join_code"])

    assignment_id = await service.create_assignment(
        pool, teacher_id, created["classroom_id"], "Practice fractions", "", "fractions", None
    )
    # unrelated-topic practice must not count
    q_other = await create_question(pool, student_id, {"question": "Q", "answer": "A", "topic": "algebra"})
    await record_attempt(pool, student_id, q_other, rating=5)
    # matching-topic practice, recorded after the assignment was posted
    q_match = await create_question(
        pool, student_id, {"question": "Q2", "answer": "A2", "topic": "fractions"}
    )
    await record_attempt(pool, student_id, q_match, rating=4)

    assignment = await pool.fetchrow(
        "SELECT assignment_id, classroom_id, title, description, topic, due_date, created_at "
        "FROM assignments WHERE assignment_id = $1",
        assignment_id,
    )
    progress = await service.get_assignment_progress(pool, created["classroom_id"], dict(assignment))
    assert len(progress) == 1
    assert progress[0]["name"] == "Prog"
    assert progress[0]["attempt_count"] == 1  # only the "fractions" attempt


async def test_assignment_with_no_topic_has_no_progress_to_show(client):
    pool = await get_pool()
    from app.auth.service import create_user

    teacher_id = await create_user(pool, "classroom-notopic-teacher@example.com", "testpassword123", "T")
    created = await service.create_classroom(pool, teacher_id, "No-topic class")
    assignment_id = await service.create_assignment(
        pool, teacher_id, created["classroom_id"], "Read the syllabus", "", "", None
    )
    assignment = await pool.fetchrow(
        "SELECT assignment_id, classroom_id, title, description, topic, due_date, created_at "
        "FROM assignments WHERE assignment_id = $1",
        assignment_id,
    )
    assert await service.get_assignment_progress(pool, created["classroom_id"], dict(assignment)) == []


async def test_assignment_progress_page_renders_for_the_owning_teacher(client):
    await signup(client, "classroom-progress-page-teacher@example.com")
    await _become_teacher(client)
    create = await client.post("/classrooms", data={"name": "Page class"}, follow_redirects=False)
    classroom_id = int(_classroom_id_from_redirect(create))
    await client.post(
        f"/classrooms/{classroom_id}/assignments",
        data={"title": "Read chapter 2", "topic": "photosynthesis"},
    )
    pool = await get_pool()
    assignment_id = await pool.fetchval(
        "SELECT assignment_id FROM assignments WHERE classroom_id = $1", classroom_id
    )

    page = await client.get(f"/classrooms/{classroom_id}/assignments/{assignment_id}")
    assert page.status_code == 200
    assert "Read chapter 2" in page.text
    assert "photosynthesis" in page.text


async def test_a_student_cannot_view_the_teacher_progress_page(client):
    pool = await get_pool()
    from app.auth.service import create_user

    teacher_id = await create_user(
        pool, "classroom-progress-secure-teacher@example.com", "testpassword123", "T"
    )
    created = await service.create_classroom(pool, teacher_id, "Secure class")
    assignment_id = await service.create_assignment(
        pool, teacher_id, created["classroom_id"], "Secret grading", "", "topic", None
    )

    await signup(client, "classroom-progress-secure-student@example.com")
    await client.post("/classrooms/join", data={"join_code": created["join_code"]})

    response = await client.get(f"/classrooms/{created['classroom_id']}/assignments/{assignment_id}")
    assert response.status_code == 404


async def test_deleting_a_classroom_cascades_members_and_assignments(client):
    await signup(client, "classroom-cascade@example.com")
    await _become_teacher(client)
    create = await client.post("/classrooms", data={"name": "Temp class"}, follow_redirects=False)
    classroom_id = int(_classroom_id_from_redirect(create))

    pool = await get_pool()
    join_code = await pool.fetchval("SELECT join_code FROM classrooms WHERE classroom_id = $1", classroom_id)
    from app.auth.service import create_user

    student_id = await create_user(pool, "classroom-cascade-student@example.com", "testpassword123", "S")
    await service.join_classroom(pool, student_id, join_code)
    assignment_id = await pool.fetchval(
        "INSERT INTO assignments (classroom_id, title) VALUES ($1, 'x') RETURNING assignment_id", classroom_id
    )

    await client.post(f"/classrooms/{classroom_id}/delete")

    remaining_classroom = await pool.fetchval(
        "SELECT classroom_id FROM classrooms WHERE classroom_id = $1", classroom_id
    )
    remaining_member = await pool.fetchval(
        "SELECT 1 FROM classroom_members WHERE classroom_id = $1", classroom_id
    )
    remaining_assignment = await pool.fetchval(
        "SELECT assignment_id FROM assignments WHERE assignment_id = $1", assignment_id
    )
    assert remaining_classroom is None
    assert remaining_member is None
    assert remaining_assignment is None
