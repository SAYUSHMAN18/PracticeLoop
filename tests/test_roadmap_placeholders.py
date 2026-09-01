import pytest

from tests.conftest import signup

# One (path, sidebar label, phase heading) tuple per Phase 5 placeholder --
# the sections the new sidebar links to that Phase 6/9/13/15 haven't built
# out yet. See app/roadmap/router.py for why these are five literal routes
# instead of a single "/{section}" catch-all.
SECTIONS = [
    ("/learning-paths", "My Learning Paths", "Phase 6"),
    ("/subjects", "Explore Subjects", "Phase 6"),
    ("/assessments", "Assessments", "Phase 9"),
    ("/projects", "Projects", "Phase 13"),
    ("/progress", "Progress", "Phase 15"),
]


@pytest.mark.parametrize("path,title,phase", SECTIONS)
async def test_placeholder_renders_its_own_title_and_phase(client, path, title, phase):
    await signup(client, f"roadmap-{path.strip('/')}@example.com")
    response = await client.get(path)
    assert response.status_code == 200
    assert f'<h1 style="margin-top:0.75rem;">{title}</h1>' in response.text
    assert phase in response.text
    assert "What's coming" in response.text


async def test_placeholders_stay_in_the_app_shell_not_a_dead_end(client):
    """Each stub should still be a real page in the app -- sidebar, topbar,
    mentor panel and all -- not a bare fragment that drops the student out
    of the shell just because the feature behind it isn't built yet."""
    await signup(client, "roadmap-shell@example.com")
    response = await client.get("/learning-paths")
    assert 'id="mentor-panel"' in response.text
    assert '<a href="/learning-paths" class="active">My Learning Paths</a>' in response.text
    assert '<a href="/dashboard">Back to Home</a>' in response.text


async def test_unknown_roadmap_style_path_is_a_real_404(client):
    await signup(client, "roadmap-404@example.com")
    response = await client.get("/not-a-real-section")
    assert response.status_code == 404
