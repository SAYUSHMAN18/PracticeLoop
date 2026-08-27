from __future__ import annotations

import asyncio
import time
from enum import Enum

from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LLMProvider(str, Enum):
    GROQ = "groq"
    GEMINI = "gemini"
    BEDROCK = "bedrock"


_last_call: float = 0.0
_lock = asyncio.Lock()


def is_configured() -> bool:
    """Whether generate() can actually be called right now -- used to decide
    between LLM-graded review and the self-rating fallback (and similar
    "degrade to something deterministic" branches) without waiting for a
    call to fail first."""
    try:
        provider = LLMProvider(settings.llm_provider.strip().lower())
    except ValueError:
        return False

    if provider is LLMProvider.GROQ:
        return bool(settings.groq_api_key.strip())
    if provider is LLMProvider.GEMINI:
        return bool(settings.gemini_api_key.strip())
    if provider is LLMProvider.BEDROCK:
        return bool(settings.bedrock_model_id.strip())
    return False


async def _pace() -> None:
    """Serialize outbound LLM calls and keep a conservative request rate."""
    global _last_call

    async with _lock:
        now = time.monotonic()
        wait = settings.llm_min_interval_seconds - (now - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()


async def generate(prompt: str, temperature: float = 0.0) -> str:
    await _pace()

    provider = LLMProvider(settings.llm_provider.strip().lower())
    started = time.monotonic()

    try:
        if provider is LLMProvider.GROQ:
            result = await _call_groq(prompt, temperature)
        elif provider is LLMProvider.GEMINI:
            result = await _call_gemini(prompt, temperature)
        elif provider is LLMProvider.BEDROCK:
            result = await _call_bedrock(prompt, temperature)
        else:
            raise RuntimeError(f"Unsupported LLM provider: {provider}")
    except Exception:
        logger.exception(
            "LLM call failed provider=%s latency_ms=%d",
            provider.value,
            int((time.monotonic() - started) * 1000),
        )
        raise

    logger.info(
        "LLM call ok provider=%s latency_ms=%d prompt_chars=%d response_chars=%d",
        provider.value,
        int((time.monotonic() - started) * 1000),
        len(prompt),
        len(result),
    )
    return result


async def _call_groq(prompt: str, temperature: float) -> str:
    try:
        from groq import AsyncGroq, RateLimitError
    except ImportError as exc:
        raise RuntimeError("LLM_PROVIDER=groq requires the groq package: pip install -e '.[groq]'") from exc

    api_key = settings.groq_api_key.strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    client = AsyncGroq(api_key=api_key)

    for attempt in range(3):
        try:
            response = await client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            break
        except RateLimitError:
            if attempt == 2:
                raise
            await asyncio.sleep(2**attempt)

    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("Groq returned an empty response.")
    return content.strip()


async def _call_gemini(prompt: str, temperature: float) -> str:
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
    return response.text.strip()


def _invoke_bedrock_sync(prompt: str, temperature: float) -> str:
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
    return content[0]["text"].strip()


async def _call_bedrock(prompt: str, temperature: float) -> str:
    try:
        import boto3  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("LLM_PROVIDER=bedrock requires boto3: pip install -e '.[bedrock]'") from exc

    # boto3 is synchronous; running invoke_model directly on the event loop
    # would block every other in-flight request for the network round trip.
    return await run_in_threadpool(_invoke_bedrock_sync, prompt, temperature)
