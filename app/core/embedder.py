from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

# scripts/schema.sql hardcodes `vector(384)` -- verify_embedding_dimension()
# catches a config/schema mismatch at startup instead of deep inside asyncpg
# on the first insert.
EXPECTED_DIMENSION = 384


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    return SentenceTransformer(settings.embedding_model, device="cpu")


def embed_text(text: str) -> list[float]:
    if not text or not text.strip():
        raise ValueError("Cannot embed empty text")

    model = get_embedding_model()
    embedding = model.encode(text.strip(), normalize_embeddings=True, convert_to_numpy=True)
    return embedding.tolist()


async def embed_text_async(text: str) -> list[float]:
    """model.encode() is synchronous CPU work; running it directly on the
    event loop would block every other in-flight request (e.g. every
    keystroke of the live search) for its duration."""
    return await run_in_threadpool(embed_text, text)


def verify_embedding_dimension() -> None:
    model = get_embedding_model()
    actual = model.get_sentence_embedding_dimension()
    if actual != EXPECTED_DIMENSION:
        raise RuntimeError(
            f"embedding_model={settings.embedding_model!r} produces {actual}-dim vectors, "
            f"but scripts/schema.sql declares vector({EXPECTED_DIMENSION}). "
            f"Update the schema's vector() dimension and re-run it against a fresh database."
        )
