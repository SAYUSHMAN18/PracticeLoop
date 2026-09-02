from __future__ import annotations

import asyncpg


async def export_data(pool: asyncpg.Pool, user_id: int) -> dict:
    """Everything a student created or that was measured about them, in
    one JSON structure they can keep -- a real "your data" export, not
    just a promise. Document content_bytes (the raw uploaded files) are
    deliberately excluded: metadata only (title, filename, type, size),
    since re-serializing binary files into JSON is the wrong shape for
    an export a person will actually read; the files themselves are
    already downloadable one at a time from the vault."""
    from app.assessments.service import list_attempts as list_diagnostic_attempts
    from app.auth.service import get_user
    from app.classrooms.service import list_classrooms_for_student, list_classrooms_for_teacher
    from app.documents.service import list_documents
    from app.gamification.service import get_xp_summary
    from app.guardian.service import list_invites_for_student, list_students_for_guardian
    from app.learning_paths.service import get_path_detail, list_paths
    from app.practice.service import list_questions
    from app.profile.service import get_profile
    from app.projects.service import get_project, list_projects

    user = await get_user(pool, user_id)
    profile = await get_profile(pool, user_id)
    questions = await list_questions(pool, user_id)
    documents = await list_documents(pool, user_id)
    paths_summary = await list_paths(pool, user_id)
    paths_detail = [await get_path_detail(pool, user_id, p["path_id"]) for p in paths_summary]
    diagnostics = await list_diagnostic_attempts(pool, user_id)
    projects_summary = await list_projects(pool, user_id)
    projects_detail = [await get_project(pool, user_id, p["project_id"]) for p in projects_summary]
    xp = await get_xp_summary(pool, user_id)
    classrooms_taught = await list_classrooms_for_teacher(pool, user_id)
    classrooms_joined = await list_classrooms_for_student(pool, user_id)
    guardian_invites_sent = await list_invites_for_student(pool, user_id)
    students_guarded = await list_students_for_guardian(pool, user_id)

    return {
        "account": {
            "user_id": user["user_id"],
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
        },
        "profile": dict(profile),
        "questions": [dict(q) for q in questions],
        "documents": [dict(d) for d in documents],
        "learning_paths": paths_detail,
        "diagnostics": [dict(d) for d in diagnostics],
        "projects": projects_detail,
        "xp_summary": xp,
        "classrooms_taught": [dict(c) for c in classrooms_taught],
        "classrooms_joined": [dict(c) for c in classrooms_joined],
        "guardian_invites_sent": [dict(g) for g in guardian_invites_sent],
        "students_you_guard": students_guarded,
    }


async def delete_account(pool: asyncpg.Pool, user_id: int) -> None:
    """Every table that references users(user_id) does so with
    ON DELETE CASCADE (verified across every migration in this app), so
    deleting the user row itself removes everything they own -- no
    separate per-table cleanup to keep in sync as new features add new
    tables."""
    await pool.execute("DELETE FROM users WHERE user_id = $1", user_id)
