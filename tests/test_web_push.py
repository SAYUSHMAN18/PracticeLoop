"""Web push (app/notifications/push.py): real OS/browser notifications on
top of the in-app bell. VAPID_PUBLIC_KEY/PRIVATE_KEY are unset in tests --
the real "not configured" state -- so push._send_one_sync (the one
function that actually calls out, via pywebpush) is monkeypatched to
exercise delivery, retry/cleanup, and the notifications.service.create()
hook without ever hitting a real push service.
"""

from __future__ import annotations

from pywebpush import WebPushException

from app.auth.service import create_user
from app.core.config import settings
from app.core.db import get_pool
from app.notifications import push, service
from tests.conftest import signup


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _configure_vapid(monkeypatch) -> None:
    monkeypatch.setattr(settings, "vapid_public_key", "test-public-key")
    monkeypatch.setattr(settings, "vapid_private_key", "test-private-key")


async def _subscribed_user(pool, email: str, *, endpoint: str = "https://push.example/ep") -> int:
    user_id = await create_user(pool, email, "testpassword123", "Push User")
    await push.add_subscription(pool, user_id, endpoint=endpoint, p256dh="p256dh-val", auth="auth-val")
    return user_id


# ---------- subscription storage ----------


async def test_add_subscription_then_remove_round_trips(client):
    pool = await get_pool()
    user_id = await create_user(pool, "push-roundtrip@example.com", "testpassword123", "P")

    await push.add_subscription(pool, user_id, endpoint="https://push.example/a", p256dh="k", auth="a")
    count = await pool.fetchval("SELECT count(*) FROM push_subscriptions WHERE user_id = $1", user_id)
    assert count == 1

    await push.remove_subscription(pool, user_id, "https://push.example/a")
    count = await pool.fetchval("SELECT count(*) FROM push_subscriptions WHERE user_id = $1", user_id)
    assert count == 0


async def test_resubscribing_the_same_endpoint_updates_instead_of_duplicating(client):
    pool = await get_pool()
    user_id = await create_user(pool, "push-resub@example.com", "testpassword123", "P")

    await push.add_subscription(pool, user_id, endpoint="https://push.example/a", p256dh="old", auth="old")
    await push.add_subscription(pool, user_id, endpoint="https://push.example/a", p256dh="new", auth="new")

    rows = await pool.fetch("SELECT p256dh FROM push_subscriptions WHERE user_id = $1", user_id)
    assert len(rows) == 1
    assert rows[0]["p256dh"] == "new"


# ---------- the subscribe/unsubscribe routes ----------


async def test_subscribe_route_saves_a_subscription(client):
    await signup(client, "push-route-sub@example.com")
    response = await client.post(
        "/notifications/push/subscribe",
        json={"endpoint": "https://push.example/route", "keys": {"p256dh": "k", "auth": "a"}},
    )
    assert response.status_code == 200

    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "push-route-sub@example.com")
    endpoint = await pool.fetchval("SELECT endpoint FROM push_subscriptions WHERE user_id = $1", user_id)
    assert endpoint == "https://push.example/route"


async def test_malformed_subscribe_payload_is_a_400_not_a_500(client):
    await signup(client, "push-route-malformed@example.com")
    response = await client.post("/notifications/push/subscribe", json={"nope": "not a subscription"})
    assert response.status_code == 400


async def test_unsubscribe_route_removes_it(client):
    await signup(client, "push-route-unsub@example.com")
    await client.post(
        "/notifications/push/subscribe",
        json={"endpoint": "https://push.example/gone", "keys": {"p256dh": "k", "auth": "a"}},
    )
    response = await client.post(
        "/notifications/push/unsubscribe", json={"endpoint": "https://push.example/gone"}
    )
    assert response.status_code == 200

    pool = await get_pool()
    user_id = await pool.fetchval(
        "SELECT user_id FROM users WHERE email = $1", "push-route-unsub@example.com"
    )
    count = await pool.fetchval("SELECT count(*) FROM push_subscriptions WHERE user_id = $1", user_id)
    assert count == 0


# ---------- sending ----------


async def test_send_is_a_noop_when_vapid_is_not_configured(client, monkeypatch):
    pool = await get_pool()
    user_id = await _subscribed_user(pool, "push-noconfig@example.com")

    def boom(*a, **k):
        raise AssertionError("should never be called when VAPID is unconfigured")

    monkeypatch.setattr(push, "_send_one_sync", boom)
    await push.send_push_to_user(pool, user_id, "Title", "Body")  # must not raise


async def test_send_posts_to_every_subscribed_device(client, monkeypatch):
    _configure_vapid(monkeypatch)
    pool = await get_pool()
    user_id = await create_user(pool, "push-multi@example.com", "testpassword123", "P")
    await push.add_subscription(pool, user_id, endpoint="https://push.example/1", p256dh="k1", auth="a1")
    await push.add_subscription(pool, user_id, endpoint="https://push.example/2", p256dh="k2", auth="a2")

    sent_to = []
    monkeypatch.setattr(
        push, "_send_one_sync", lambda sub_info, payload: sent_to.append(sub_info["endpoint"])
    )

    await push.send_push_to_user(pool, user_id, "New assignment", "Read chapter 3", link="/classrooms")
    assert sorted(sent_to) == ["https://push.example/1", "https://push.example/2"]


async def test_a_410_response_drops_the_dead_subscription(client, monkeypatch):
    _configure_vapid(monkeypatch)
    pool = await get_pool()
    user_id = await _subscribed_user(pool, "push-gone@example.com")

    def raise_gone(sub_info, payload):
        raise WebPushException("gone", response=_FakeResponse(410))

    monkeypatch.setattr(push, "_send_one_sync", raise_gone)
    await push.send_push_to_user(pool, user_id, "Title", "Body")  # must not raise

    count = await pool.fetchval("SELECT count(*) FROM push_subscriptions WHERE user_id = $1", user_id)
    assert count == 0


async def test_a_server_error_is_swallowed_but_the_subscription_survives(client, monkeypatch):
    _configure_vapid(monkeypatch)
    pool = await get_pool()
    user_id = await _subscribed_user(pool, "push-servererror@example.com")

    def raise_500(sub_info, payload):
        raise WebPushException("server error", response=_FakeResponse(500))

    monkeypatch.setattr(push, "_send_one_sync", raise_500)
    await push.send_push_to_user(pool, user_id, "Title", "Body")  # must not raise

    count = await pool.fetchval("SELECT count(*) FROM push_subscriptions WHERE user_id = $1", user_id)
    assert count == 1  # a transient failure isn't grounds to unsubscribe the user


# ---------- the create() hook ----------


async def test_creating_a_notification_pushes_when_vapid_is_configured(client, monkeypatch):
    _configure_vapid(monkeypatch)
    pool = await get_pool()
    user_id = await _subscribed_user(pool, "push-hook@example.com")

    calls = []
    monkeypatch.setattr(push, "_send_one_sync", lambda sub_info, payload: calls.append(payload))

    await service.create(
        pool, user_id, "assignment_created", "New assignment", body="Details", link="/classrooms"
    )
    assert len(calls) == 1
    assert "New assignment" in calls[0]


async def test_creating_a_notification_without_vapid_configured_still_succeeds(client):
    """The default test/dev state -- push must never be why an in-app
    notification fails to record."""
    pool = await get_pool()
    user_id = await create_user(pool, "push-hook-noconfig@example.com", "testpassword123", "P")
    notification_id = await service.create(pool, user_id, "assignment_created", "New assignment")
    assert notification_id is not None
