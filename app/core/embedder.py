from __future__ import annotations

from functools import lru_cache

import torch
from sentence_transformers import SentenceTransformer
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

# Belt-and-suspenders alongside the Dockerfile's OMP_NUM_THREADS=1: torch's
# own intraop thread pool isn't fully governed by that env var in every
# build, and each thread's buffers add up fast on a memory-constrained
# instance (this app never benefits from intra-op parallelism anyway --
# encode() calls are single small strings, not large batches).
torch.set_num_threads(1)

# migrations/0001_baseline.sql hardcodes `vector(384)` -- verify_embedding_dimension()
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
    # get_sentence_embedding_dimension was renamed to get_embedding_dimension
    # in newer sentence-transformers; pyproject.toml pins no upper bound, so
    # support whichever name the installed version has.
    get_dimension = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
    actual = get_dimension()
    if actual != EXPECTED_DIMENSION:
        raise RuntimeError(
            f"embedding_model={settings.embedding_model!r} produces {actual}-dim vectors, "
            f"but migrations/0001_baseline.sql declares vector({EXPECTED_DIMENSION}). "
            f"Add a migration that changes the column's vector() dimension."
        )
