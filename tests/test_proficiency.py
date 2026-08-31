from tests.conftest import signup


async def test_proficiency_roundtrip_via_profile(client):
    await signup(client, "prof@example.com")

    saved = await client.post(
        "/profile", data={"target_role": "", "target_companies": "", "proficiency_level": "intermediate"}
    )
    assert saved.status_code == 200
    assert '<option value="intermediate" selected>Comfortable with the basics</option>' in saved.text


async def test_unknown_proficiency_falls_back_to_not_set(client):
    await signup(client, "prof-bad@example.com")
    response = await client.post(
        "/profile", data={"target_role": "", "target_companies": "", "proficiency_level": "expert-hacker"}
    )
    assert response.status_code == 200
    assert '<option value="" selected>Not set</option>' in response.text


async def test_proficiency_settable_from_welcome_screen(client):
    await client.post(
        "/signup",
        data={"name": "Prof Welcome", "email": "prof-welcome@example.com", "password": "testpassword123"},
    )
    saved = await client.post("/welcome", data={"proficiency_level": "beginner"})
    assert saved.status_code == 303

    profile = await client.get("/profile")
    assert '<option value="beginner" selected>Just getting started</option>' in profile.text


async def test_resume_upload_does_not_wipe_proficiency_level(client):
    """Regression guard: update_profile's resume-upload call site
    (documents/router.py) must pass through the existing proficiency_level,
    not silently reset it to "" the way a missed keyword default would."""
    await signup(client, "prof-resume@example.com")
    await client.post(
        "/profile", data={"target_role": "", "target_companies": "", "proficiency_level": "advanced"}
    )

    files = {"file": ("resume.txt", b"Some resume content here.", "text/plain")}
    upload = await client.post("/documents", data={"doc_type": "resume"}, files=files)
    assert upload.status_code == 200

    profile = await client.get("/profile")
    assert '<option value="advanced" selected>Advanced -- refining and filling gaps</option>' in profile.text
