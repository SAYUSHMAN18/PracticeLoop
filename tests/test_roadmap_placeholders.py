from tests.conftest import signup

# Progress (Phase 15) is the only Phase 5 stub left -- My Learning Paths,
# Explore Subjects (Phase 6), Assessments (Phase 9), and Projects
# (Phase 13) all moved out of this file once they became real pages,
# covered by their own test files instead.


async def test_progress_placeholder_renders_its_title_and_phase(client):
    await signup(client, "roadmap-progress@example.com")
    response = await client.get("/progress")
    assert response.status_code == 200
    assert '<h1 style="margin-top:0.75rem;">Progress</h1>' in response.text
    assert "Phase 15" in response.text
    assert "What's coming" in response.text


async def test_placeholder_stays_in_the_app_shell_not_a_dead_end(client):
    """The stub should still be a real page in the app -- sidebar, topbar,
    mentor panel and all -- not a bare fragment that drops the student out
    of the shell just because the feature behind it isn't built yet."""
    await signup(client, "roadmap-shell@example.com")
    response = await client.get("/progress")
    assert 'id="mentor-panel"' in response.text
    assert '<a href="/progress" class="active">Progress</a>' in response.text
    assert '<a href="/dashboard">Back to Home</a>' in response.text


async def test_unknown_roadmap_style_path_is_a_real_404(client):
    await signup(client, "roadmap-404@example.com")
    response = await client.get("/not-a-real-section")
    assert response.status_code == 404
