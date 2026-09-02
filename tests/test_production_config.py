"""Boot-time production guards, and the backup script's pure helpers.

verify_production_config exists to turn "deployed wrong" into "refused to
start" for the two cases where running anyway is worse than not running.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from app.core import config
from app.core.config import (
    DEFAULT_DATABASE_URL,
    DEFAULT_SESSION_SECRET,
    settings,
    verify_production_config,
)


@pytest.fixture
def prod(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "session_secret", "a-real-random-secret")
    monkeypatch.setattr(settings, "database_url", "postgresql://real-host/practiceloop")


def test_development_never_raises(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "session_secret", DEFAULT_SESSION_SECRET)
    monkeypatch.setattr(settings, "database_url", DEFAULT_DATABASE_URL)
    verify_production_config()


def test_production_refuses_the_default_session_secret(prod, monkeypatch):
    """Every session cookie would be forgeable by anyone who has read the repo."""
    monkeypatch.setattr(settings, "session_secret", DEFAULT_SESSION_SECRET)
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        verify_production_config()


def test_production_refuses_the_default_database_url(prod, monkeypatch):
    monkeypatch.setattr(settings, "database_url", DEFAULT_DATABASE_URL)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        verify_production_config()


def test_both_problems_are_reported_together(prod, monkeypatch):
    """One restart per problem is a miserable way to fix a deploy."""
    monkeypatch.setattr(settings, "session_secret", DEFAULT_SESSION_SECRET)
    monkeypatch.setattr(settings, "database_url", DEFAULT_DATABASE_URL)
    with pytest.raises(RuntimeError) as exc:
        verify_production_config()
    assert "SESSION_SECRET" in str(exc.value)
    assert "DATABASE_URL" in str(exc.value)


def test_a_missing_llm_provider_warns_but_still_boots(prod, caplog):
    """An AI-free deploy is supported -- most of the app degrades to a real
    deterministic path -- but it silently turns diagnostics and Writing Lab
    off entirely, so it belongs in the logs, not in a user's bug report."""
    with caplog.at_level("WARNING"):
        verify_production_config()
    assert any("No LLM provider is configured" in r.message for r in caplog.records)


def test_error_reporting_is_a_noop_without_a_dsn(monkeypatch):
    monkeypatch.setattr(settings, "sentry_dsn", "")
    config.configure_error_reporting()  # must not raise or import sentry_sdk


# --- scripts/backup.py pure helpers -------------------------------------


def _backup_module():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import backup

    return backup


def test_restore_orders_parents_before_children():
    """Postgres foreign keys are NOT DEFERRABLE by default, so a restore that
    walks tables alphabetically fails the moment `applications` lands before
    `users`."""
    backup = _backup_module()
    tables = ["applications", "attempts", "questions", "users"]
    deps = {
        "applications": ["users"],
        "attempts": ["questions", "users"],
        "questions": ["users"],
    }
    order = backup._insert_order(tables, deps)
    assert order.index("users") < order.index("questions")
    assert order.index("questions") < order.index("attempts")
    assert order.index("users") < order.index("applications")
    assert sorted(order) == sorted(tables)


def test_insert_order_does_not_hang_on_a_cycle():
    backup = _backup_module()
    order = backup._insert_order(["a", "b"], {"a": ["b"], "b": ["a"]})
    assert sorted(order) == ["a", "b"]


def test_values_round_trip_through_json():
    """JSON has no datetime, so every temporal column would come back as a
    string and asyncpg would reject it on the way in."""
    backup = _backup_module()
    moment = datetime.fromisoformat("2026-08-27T06:30:07.568676+00:00")
    assert backup._decode(backup._encode(moment), "timestamp with time zone") == moment
    assert backup._decode(backup._encode(date(2026, 8, 27)), "date") == date(2026, 8, 27)
    assert backup._decode(backup._encode(b"\x00\x01"), "bytea") == b"\x00\x01"
    assert json.loads(backup._decode({"a": 1}, "jsonb")) == {"a": 1}
    assert backup._decode(None, "date") is None


def test_jsonb_and_vector_get_an_explicit_cast():
    """Both arrive as text on a connection with no codec registered."""
    backup = _backup_module()
    assert backup._placeholder(1, "jsonb") == "$1::jsonb"
    assert backup._placeholder(2, "vector(384)") == "$2::vector(384)"
    assert backup._placeholder(3, "text") == "$3"
