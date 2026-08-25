from __future__ import annotations

import asyncio
import time
from enum import Enum

from app.core.config import settings


class LLMProvider(str, Enum):
    GROQ = "groq"
    GEMINI = "gemini"
    BEDROCK = "bedrock"


_last_call: float = 0.0
_lock = asyncio.Lock()


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

    if provider is LLMProvider.GROQ:
        return await _call_groq(prompt, temperature)
    if provider is LLMProvider.GEMINI:
        return await _call_gemini(prompt, temperature)
    if provider is LLMProvider.BEDROCK:
        return await _call_bedrock(prompt, temperature)

    raise RuntimeError(f"Unsupported LLM provider: {provider}")


async def _call_groq(prompt: str, temperature: float) -> str:
    from groq import AsyncGroq

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
    return content.strip()


async def _call_gemini(prompt: str, temperature: float) -> str:
    import google.generativeai as genai

    api_key = settings.gemini_api_key.strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    response = await model.generate_content_async(
        prompt,
        generation_config={"temperature": temperature},
    )
    if not response.text:
        raise RuntimeError("Gemini returned an empty response.")
    return response.text.strip()


async def _call_bedrock(prompt: str, temperature: float) -> str:
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
