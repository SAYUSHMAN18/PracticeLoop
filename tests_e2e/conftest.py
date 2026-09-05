"""Fixtures for the Playwright smoke suite -- a real browser against a real
running server, unlike tests/ (which drives the ASGI app in-process via
httpx and never renders a page or executes a line of JS/CSS). That gap is
exactly what let a real regression ship silently once already: a mid-edit
CSS change broke the mobile sidebar drawer, and nothing in tests/ could
have caught it since none of it renders a page. This suite exists to close
that gap for the handful of things that only a real browser can verify --
layout, visibility, client-side JS wiring -- not to duplicate tests/'s
coverage of behavior and data.

Deliberately a separate directory, not part of tests/: it needs Playwright
browser binaries installed (`playwright install chromium`) and a real
server listening on a real port, neither of which the normal `pytest tests/`
run should have to know about. See pyproject.toml's `testpaths = ["tests"]`
-- a bare `pytest` never picks this directory up by accident.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PORT = 8931
BASE_URL = f"http://127.0.0.1:{_PORT}"


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="session")
def live_server():
    """Applies migrations, then runs the real app under uvicorn as a
    subprocess -- a real HTTP server on a real port, since a browser (unlike
    tests/'s httpx+ASGITransport client) cannot talk to an in-process ASGI
    app directly. Session-scoped: one server for the whole file, not one
    per test."""
    if _port_in_use(_PORT):
        raise RuntimeError(f"Port {_PORT} is already in use -- stop whatever's on it first.")

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    migrate = subprocess.run(
        [sys.executable, "scripts/migrate.py"], cwd=_REPO_ROOT, env=env, capture_output=True, text=True
    )
    if migrate.returncode != 0:
        raise RuntimeError(f"Migration failed:\n{migrate.stdout}\n{migrate.stderr}")

    # A real file, not subprocess.PIPE: the health-check loop below only
    # reads this on failure, and a pipe nobody drains fills its OS buffer
    # once the startup logging (the embedding model's HTTP chatter alone
    # is dozens of lines) exceeds it -- the child then blocks on its own
    # stdout write and /healthz never gets a chance to come up at all.
    log_path = _REPO_ROOT / "tests_e2e" / ".server.log"
    log_file = log_path.open("w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(_PORT)],
        cwd=_REPO_ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 60
        last_error = None
        while time.monotonic() < deadline:
            if server.poll() is not None:
                log_file.close()
                raise RuntimeError(f"Server exited early:\n{log_path.read_text(encoding='utf-8')}")
            try:
                response = httpx.get(f"{BASE_URL}/healthz", timeout=2)
                if response.status_code == 200:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
            time.sleep(0.5)
        else:
            log_file.close()
            raise RuntimeError(
                f"Server never became healthy (last error: {last_error}). Log:\n"
                f"{log_path.read_text(encoding='utf-8')}"
            )

        yield BASE_URL
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log_file.close()


@pytest.fixture()
def signed_up_page(live_server, page):
    """A fresh signup, landed on /welcome or /dashboard -- the common
    starting point for every test in this suite that needs to be logged in."""
    import uuid

    email = f"e2e-{uuid.uuid4().hex[:12]}@example.com"
    page.goto(f"{live_server}/signup")
    page.fill("#f-name", "E2E Tester")
    page.fill("#f-email", email)
    page.fill("#f-password", "testpassword123")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    return page
