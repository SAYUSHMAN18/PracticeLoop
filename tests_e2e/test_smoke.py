"""A deliberately small set of real-browser checks for the handful of
things tests/ structurally cannot see: whether an element is actually
*visible* after a click, whether a responsive layout actually responds.
Everything about correctness of data and business logic belongs in
tests/ instead -- this suite only exists to catch the CSS/JS class of
regression tests/ is blind to.
"""

from __future__ import annotations

import uuid

from playwright.sync_api import expect

from tests_e2e.conftest import BASE_URL


def _signup(page, live_server: str) -> None:
    page.goto(f"{live_server}/signup")
    page.fill("#f-name", "Smoke Tester")
    page.fill("#f-email", f"e2e-smoke-{uuid.uuid4().hex[:12]}@example.com")
    page.fill("#f-password", "testpassword123")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")


def test_signup_reaches_the_app_with_no_console_errors(live_server, page):
    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: console_errors.append(str(exc)))

    _signup(page, live_server)

    assert "/login" not in page.url
    assert console_errors == []


def test_mentor_widget_opens_and_closes(signed_up_page):
    page = signed_up_page
    page.goto(f"{BASE_URL}/dashboard")

    fab = page.locator("#mentor-toggle")
    panel = page.locator("#mentor-panel")

    expect(fab).to_be_visible()
    expect(panel).not_to_be_visible()  # closed by default, see the Loop Mentor redesign

    fab.click()
    expect(panel).to_be_visible()
    expect(fab).to_have_attribute("aria-expanded", "true")

    page.locator("#mentor-close").click()
    expect(panel).not_to_be_visible()


def test_mobile_sidebar_drawer_opens_and_closes(signed_up_page):
    """Regression coverage for the exact bug a mid-session CSS edit
    introduced and only manual browser testing caught: below 860px the
    sidebar must start off-screen and slide in on the hamburger click,
    not sit permanently docked and squeeze the reading column."""
    page = signed_up_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{BASE_URL}/dashboard")

    sidebar = page.locator("#sidebar")
    toggle = page.locator("#sidebar-toggle")
    expect(toggle).to_be_visible()

    box_closed = sidebar.bounding_box()
    assert box_closed is not None
    assert box_closed["x"] + box_closed["width"] <= 5, (
        f"Sidebar should be off-screen (closed) by default on a 390px viewport, "
        f"got x={box_closed['x']}, width={box_closed['width']}"
    )

    toggle.click()
    page.wait_for_timeout(350)  # matches the 0.25s CSS transition in style.css
    box_open = sidebar.bounding_box()
    assert box_open is not None
    assert box_open["x"] >= 0, f"Sidebar should have slid on-screen after opening, got x={box_open['x']}"


def test_key_pages_load_without_console_errors(signed_up_page):
    page = signed_up_page
    console_errors = []
    page.on(
        "console",
        lambda msg: console_errors.append(f"{page.url}: {msg.text}") if msg.type == "error" else None,
    )

    for path in ("/dashboard", "/practice", "/decks", "/learning-paths", "/jobs"):
        page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
        assert page.locator("body").is_visible()

    assert console_errors == [], f"Console errors: {console_errors}"
