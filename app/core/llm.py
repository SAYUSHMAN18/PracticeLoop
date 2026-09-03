from __future__ import annotations

import asyncio
import hashlib
import time
from collections import defaultdict
from enum import Enum

from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LLMProvider(str, Enum):
    GROQ = "groq"
    GEMINI = "gemini"
    BEDROCK = "bedrock"


class LLMResult:
    """What a provider call returns: the text plus whatever usage the
    provider was willing to tell us. Token counts are best-effort -- Groq
    and Gemini report them, Bedrock reports them in its own shape, and any
    of them can omit them."""

    __slots__ = ("text", "prompt_tokens", "completion_tokens")

    def __init__(self, text: str, prompt_tokens: int | None = None, completion_tokens: int | None = None):
        self.text = text
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


# --- concurrency ------------------------------------------------------------
#
# The old design was one asyncio.Lock plus a hard 2.1s floor: every call in
# the process ran strictly one at a time, so 30 students opening a lesson at
# once meant the 30th waited ~63s before their request even started. Now:
#
#   * a global semaphore caps how many calls are in flight at once (protects
#     the provider's rate limit and this box's memory),
#   * a per-user semaphore stops any one account -- or one runaway request
#     generating a card per skill gap -- from taking every global slot,
#   * a small min-interval between call *starts* smooths bursts without
#     serializing anything.
#
# _bootstrap() reads the sizes once (settings are immutable after import);
# tests that tune them re-run it.

_global_sem: asyncio.Semaphore | None = None
_user_sems: dict[int, asyncio.Semaphore] = {}
_pace_lock = asyncio.Lock()
_last_start: float = 0.0
_ANON = -1  # user_id for calls made with no user attached (cron, scripts)


def _bootstrap() -> None:
    global _global_sem, _user_sems
    _global_sem = asyncio.Semaphore(max(1, settings.llm_max_concurrency))
    _user_sems = defaultdict(lambda: asyncio.Semaphore(max(1, settings.llm_per_user_concurrency)))


def _user_sem(user_id: int | None) -> asyncio.Semaphore:
    if _global_sem is None:
        _bootstrap()
    return _user_sems[user_id if user_id is not None else _ANON]


async def _pace() -> None:
    """Space call starts by llm_min_interval_seconds. Held only long enough
    to read and write the timestamp -- it does not gate the call itself, so
    other calls proceed in parallel up to the semaphore limit."""
    global _last_start
    async with _pace_lock:
        wait = settings.llm_min_interval_seconds - (time.monotonic() - _last_start)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_start = time.monotonic()


def is_configured() -> bool:
    """Whether generate() can actually be called right now -- used to decide
    between LLM-graded review and the self-rating fallback (and similar
    "degrade to something deterministic" branches) without waiting for a
    call to fail first."""
    return _provider_ready(settings.llm_provider)


def _provider_ready(name: str) -> bool:
    try:
        provider = LLMProvider(name.strip().lower())
    except ValueError:
        return False
    if provider is LLMProvider.GROQ:
        return bool(settings.groq_api_key.strip())
    if provider is LLMProvider.GEMINI:
        return bool(settings.gemini_api_key.strip())
    if provider is LLMProvider.BEDROCK:
        return bool(settings.bedrock_model_id.strip())
    return False


def _model_for(provider: LLMProvider) -> str:
    if provider is LLMProvider.GROQ:
        return settings.groq_model
    if provider is LLMProvider.GEMINI:
        return settings.gemini_model
    return settings.bedrock_model_id


def _cache_key(provider: LLMProvider, temperature: float, prompt: str) -> str:
    raw = f"{provider.value}|{_model_for(provider)}|{temperature}|{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _cache_get(key: str) -> str | None:
    from app.core.db import get_pool

    pool = await get_pool()
    row = await pool.fetchrow(
        """UPDATE llm_cache
           SET hit_count = hit_count + 1, last_hit_at = now()
           WHERE cache_key = $1
             AND created_at > now() - ($2 || ' days')::interval
           RETURNING response""",
        key,
        str(settings.llm_cache_ttl_days),
    )
    return row["response"] if row else None


async def _cache_put(key: str, response: str, model: str) -> None:
    from app.core.db import get_pool

    pool = await get_pool()
    await pool.execute(
        """INSERT INTO llm_cache (cache_key, response, model)
           VALUES ($1, $2, $3)
           ON CONFLICT (cache_key) DO UPDATE
             SET response = EXCLUDED.response, model = EXCLUDED.model,
                 created_at = now(), hit_count = 0, last_hit_at = NULL""",
        key,
        response,
        model,
    )


async def _record_call(
    *,
    user_id: int | None,
    provider: str,
    model: str,
    result: LLMResult | None,
    cached: bool,
    failed: bool,
    latency_ms: int,
) -> None:
    """Never let telemetry break a user request -- a failed insert here logs
    and is swallowed."""
    try:
        from app.core.db import get_pool

        pool = await get_pool()
        await pool.execute(
            """INSERT INTO llm_calls
                   (user_id, provider, model, prompt_tokens, completion_tokens, cached, failed, latency_ms)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            user_id,
            provider,
            model,
            result.prompt_tokens if result else None,
            result.completion_tokens if result else None,
            cached,
            failed,
            latency_ms,
        )
    except Exception:
        logger.warning("llm_calls insert failed", exc_info=True)


async def generate(
    prompt: str,
    temperature: float = 0.0,
    *,
    user_id: int | None = None,
    cacheable: bool = False,
) -> str:
    """Run one LLM completion.

    `cacheable=True` is passed only by call sites whose prompt contains no
    user data (learning-path skeletons, lesson bodies, diagnostics) -- an
    identical prompt from another user is then served from llm_cache for
    free. Grading and mentor replies leave it False.

    `user_id` attributes the call in llm_calls for per-user cost reporting;
    it also picks the per-user concurrency slot.
    """
    primary = LLMProvider(settings.llm_provider.strip().lower())

    if cacheable:
        key = _cache_key(primary, temperature, prompt)
        hit = await _cache_get(key)
        if hit is not None:
            await _record_call(
                user_id=user_id,
                provider=primary.value,
                model=_model_for(primary),
                result=LLMResult(hit),
                cached=True,
                failed=False,
                latency_ms=0,
            )
            return hit

    async with _global_sem_ctx(), _user_sem(user_id):
        await _pace()
        text = await _dispatch(prompt, temperature, primary, user_id)

    if cacheable:
        await _cache_put(_cache_key(primary, temperature, prompt), text, _model_for(primary))

    return text


def _global_sem_ctx() -> asyncio.Semaphore:
    if _global_sem is None:
        _bootstrap()
    assert _global_sem is not None
    return _global_sem


async def _dispatch(prompt: str, temperature: float, primary: LLMProvider, user_id: int | None) -> str:
    """Try the configured provider (with retries); on a hard failure, try the
    configured fallback once. Every attempt -- success or failure -- lands a
    row in llm_calls."""
    order: list[LLMProvider] = [primary]
    fallback_name = settings.llm_fallback_provider.strip().lower()
    if fallback_name and fallback_name != primary.value and _provider_ready(fallback_name):
        try:
            order.append(LLMProvider(fallback_name))
        except ValueError:
            pass

    last_exc: Exception | None = None
    for provider in order:
        started = time.monotonic()
        try:
            result = await _with_retry(provider, prompt, temperature)
        except Exception as exc:  # noqa: BLE001 -- recorded, then re-raised or fallen through
            last_exc = exc
            latency = int((time.monotonic() - started) * 1000)
            logger.warning("LLM call failed provider=%s latency_ms=%d: %s", provider.value, latency, exc)
            await _record_call(
                user_id=user_id,
                provider=provider.value,
                model=_model_for(provider),
                result=None,
                cached=False,
                failed=True,
                latency_ms=latency,
            )
            continue

        latency = int((time.monotonic() - started) * 1000)
        logger.info(
            "LLM call ok provider=%s latency_ms=%d prompt_chars=%d response_chars=%d",
            provider.value,
            latency,
            len(prompt),
            len(result.text),
        )
        await _record_call(
            user_id=user_id,
            provider=provider.value,
            model=_model_for(provider),
            result=result,
            cached=False,
            failed=False,
            latency_ms=latency,
        )
        return result.text

    assert last_exc is not None
    raise last_exc


# Substrings that mark a permanent failure -- retrying it just adds
# 1s + 2s of backoff before the same error, and delays the deterministic
# fallback the caller has waiting. A missing key or an uninstalled SDK
# won't fix itself between attempts.
_PERMANENT_ERROR_MARKERS = (
    "not configured",
    "requires the",
    "pip install",
    "unsupported llm provider",
    "api key is not",
)


def _is_permanent(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _PERMANENT_ERROR_MARKERS)


async def _with_retry(provider: LLMProvider, prompt: str, temperature: float) -> LLMResult:
    """Three attempts with exponential backoff, for every provider -- a
    transient 5xx, a rate-limit, or a dropped connection from any of them
    is worth retrying. A *permanent* error (no key, no SDK) is raised
    immediately: retrying it only delays the caller's fallback."""
    attempts = 3
    for attempt in range(attempts):
        try:
            if provider is LLMProvider.GROQ:
                return await _call_groq(prompt, temperature)
            if provider is LLMProvider.GEMINI:
                return await _call_gemini(prompt, temperature)
            if provider is LLMProvider.BEDROCK:
                return await _call_bedrock(prompt, temperature)
            raise RuntimeError(f"Unsupported LLM provider: {provider}")
        except Exception as exc:
            if attempt == attempts - 1 or _is_permanent(exc):
                raise
            await asyncio.sleep(2**attempt)
    raise RuntimeError("unreachable")


async def _call_groq(prompt: str, temperature: float) -> LLMResult:
    try:
        from groq import AsyncGroq
    except ImportError as exc:
        raise RuntimeError("LLM_PROVIDER=groq requires the groq package: pip install -e '.[groq]'") from exc

    api_key = settings.groq_api_key.strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    client = AsyncGroq(api_key=api_key)
    response = await client.chat.completions.create(
        model=settings.groq_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("Groq returned an empty response.")
    usage = getattr(response, "usage", None)
    return LLMResult(
        content.strip(),
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
    )


async def _call_gemini(prompt: str, temperature: float) -> LLMResult:
    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise RuntimeError(
            "LLM_PROVIDER=gemini requires google-generativeai: pip install -e '.[gemini]'"
        ) from exc

    api_key = settings.gemini_api_key.strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(settings.gemini_model)
    response = await model.generate_content_async(
        prompt,
        generation_config={"temperature": temperature},
    )
    if not response.text:
        raise RuntimeError("Gemini returned an empty response.")
    meta = getattr(response, "usage_metadata", None)
    return LLMResult(
        response.text.strip(),
        prompt_tokens=getattr(meta, "prompt_token_count", None),
        completion_tokens=getattr(meta, "candidates_token_count", None),
    )


def _invoke_bedrock_sync(prompt: str, temperature: float) -> LLMResult:
    import json

    import boto3

    model_id = settings.bedrock_model_id.strip()
    if not model_id:
        raise RuntimeError("BEDROCK_MODEL_ID is not configured.")

    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
        ),
    )
    payload = json.loads(response["body"].read())
    content = payload.get("content", [])
    if not content or not content[0].get("text"):
        raise RuntimeError("Bedrock returned an empty response.")
    usage = payload.get("usage", {})
    return LLMResult(
        content[0]["text"].strip(),
        prompt_tokens=usage.get("input_tokens"),
        completion_tokens=usage.get("output_tokens"),
    )


async def _call_bedrock(prompt: str, temperature: float) -> LLMResult:
    try:
        import boto3  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("LLM_PROVIDER=bedrock requires boto3: pip install -e '.[bedrock]'") from exc

    # boto3 is synchronous; running invoke_model directly on the event loop
    # would block every other in-flight request for the network round trip.
    return await run_in_threadpool(_invoke_bedrock_sync, prompt, temperature)
