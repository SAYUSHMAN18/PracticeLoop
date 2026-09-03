"""The AI transport layer: concurrency, caching, failover, accounting.

The old design was one process-wide lock plus a hard 2.1s floor -- 30
students opening a lesson at once meant the 30th waited ~63s before their
request even started. These tests pin the replacement: parallel calls up
to a limit, a per-user cap so one account can't take every slot, a shared
cache for prompts that carry no user data, automatic failover, and a row
in llm_calls for every call so spend is visible.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core import llm
from app.core.config import settings
from app.core.db import get_pool


@pytest.fixture(autouse=True)
async def _clean_llm_tables():
    pool = await get_pool()
    await pool.execute("TRUNCATE llm_cache, llm_calls RESTART IDENTITY")
    yield
    await pool.execute("TRUNCATE llm_cache, llm_calls RESTART IDENTITY")


@pytest.fixture
async def users():
    """Ten real user rows so llm_calls' user_id FK is satisfied for any
    user_id in 1..10. _clean_tables (conftest) truncates users after each
    test, so this recreates them per test."""
    from app.auth.service import create_user

    pool = await get_pool()
    ids = {}
    for n in range(1, 11):
        ids[n] = await create_user(pool, f"llm{n}@example.com", "testpassword123", f"U{n}")
    return ids


@pytest.fixture
def fast_pacing(monkeypatch):
    monkeypatch.setattr(settings, "llm_min_interval_seconds", 0.0)


def _stub_provider(monkeypatch, impl):
    """Replace every provider entrypoint with one coroutine returning an
    llm.LLMResult (or raising). Keeps _dispatch / _with_retry / the cache /
    the semaphores -- i.e. everything under test -- real."""
    monkeypatch.setattr(llm, "_call_groq", impl)
    monkeypatch.setattr(llm, "_call_gemini", impl)
    monkeypatch.setattr(llm, "_call_bedrock", impl)


async def test_calls_run_in_parallel_up_to_the_global_limit(monkeypatch, fast_pacing, users):
    monkeypatch.setattr(settings, "llm_max_concurrency", 3)
    monkeypatch.setattr(settings, "llm_per_user_concurrency", 3)
    llm._bootstrap()

    in_flight = 0
    peak = 0

    async def slow(prompt, temperature):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.15)
        in_flight -= 1
        return llm.LLMResult("ok")

    _stub_provider(monkeypatch, slow)

    started = time.monotonic()
    await asyncio.gather(*(llm.generate(f"q{i}", user_id=i + 1) for i in range(6)))
    elapsed = time.monotonic() - started

    assert peak == 3, f"expected 3 concurrent, saw {peak}"
    # 6 calls / 3 lanes * 0.15s ~= 0.30s; the old serial design was 6*0.15 + 5*2.1.
    assert elapsed < 0.9


async def test_one_user_cannot_take_every_slot(monkeypatch, fast_pacing, users):
    monkeypatch.setattr(settings, "llm_max_concurrency", 4)
    monkeypatch.setattr(settings, "llm_per_user_concurrency", 1)
    llm._bootstrap()

    per_user_peak: dict[int, int] = {}
    live: dict[int, int] = {}

    async def track(prompt, temperature):
        uid = int(prompt)
        live[uid] = live.get(uid, 0) + 1
        per_user_peak[uid] = max(per_user_peak.get(uid, 0), live[uid])
        await asyncio.sleep(0.1)
        live[uid] -= 1
        return llm.LLMResult("ok")

    _stub_provider(monkeypatch, track)

    # user 1 fires 4 calls, user 2 fires 1 -- user 2 must not be starved.
    await asyncio.gather(
        *(llm.generate("1", user_id=1) for _ in range(4)),
        llm.generate("2", user_id=2),
    )
    assert per_user_peak[1] == 1  # capped
    assert per_user_peak.get(2) == 1


async def test_a_cacheable_prompt_is_served_from_cache_the_second_time(monkeypatch, fast_pacing, users):
    llm._bootstrap()
    calls = 0

    async def counting(prompt, temperature):
        nonlocal calls
        calls += 1
        return llm.LLMResult(f"answer {calls}", prompt_tokens=10, completion_tokens=5)

    _stub_provider(monkeypatch, counting)

    first = await llm.generate("shared prompt", temperature=0.0, cacheable=True, user_id=1)
    second = await llm.generate("shared prompt", temperature=0.0, cacheable=True, user_id=2)

    assert first == second == "answer 1"
    assert calls == 1  # the provider was hit once

    pool = await get_pool()
    rows = await pool.fetch("SELECT cached FROM llm_calls ORDER BY call_id")
    assert [r["cached"] for r in rows] == [False, True]


async def test_a_non_cacheable_prompt_is_never_stored(monkeypatch, fast_pacing):
    llm._bootstrap()

    async def ok(prompt, temperature):
        return llm.LLMResult("reply")

    _stub_provider(monkeypatch, ok)

    await llm.generate("personal thing", cacheable=False)
    await llm.generate("personal thing", cacheable=False)

    pool = await get_pool()
    assert await pool.fetchval("SELECT count(*) FROM llm_cache") == 0
    assert await pool.fetchval("SELECT count(*) FROM llm_calls") == 2


async def test_failover_tries_the_fallback_provider_once(monkeypatch, fast_pacing, users):
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "llm_fallback_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "set")
    llm._bootstrap()

    async def groq_down(prompt, temperature):
        raise RuntimeError("groq exploded")

    async def gemini_ok(prompt, temperature):
        return llm.LLMResult("from gemini")

    monkeypatch.setattr(llm, "_call_groq", groq_down)
    monkeypatch.setattr(llm, "_call_gemini", gemini_ok)

    out = await llm.generate("q", user_id=7)
    assert out == "from gemini"

    pool = await get_pool()
    rows = await pool.fetch("SELECT provider, failed FROM llm_calls ORDER BY call_id")
    assert (rows[0]["provider"], rows[0]["failed"]) == ("groq", True)
    assert (rows[-1]["provider"], rows[-1]["failed"]) == ("gemini", False)


async def test_a_transient_error_is_retried_then_succeeds(monkeypatch, fast_pacing):
    monkeypatch.setattr(settings, "llm_fallback_provider", "")
    llm._bootstrap()
    attempts = 0

    async def flaky(prompt, temperature):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("transient 503")
        return llm.LLMResult("finally")

    _stub_provider(monkeypatch, flaky)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(llm.asyncio, "sleep", lambda *_: real_sleep(0))  # skip the backoff wait

    assert await llm.generate("q") == "finally"
    assert attempts == 3


async def test_every_call_records_tokens_when_the_provider_reports_them(monkeypatch, fast_pacing, users):
    llm._bootstrap()

    async def with_usage(prompt, temperature):
        return llm.LLMResult("hi", prompt_tokens=120, completion_tokens=40)

    _stub_provider(monkeypatch, with_usage)
    await llm.generate("q", user_id=3)

    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM llm_calls ORDER BY call_id DESC LIMIT 1")
    assert row["prompt_tokens"] == 120
    assert row["completion_tokens"] == 40
    assert row["user_id"] == 3
    assert row["failed"] is False
