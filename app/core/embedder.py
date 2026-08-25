from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.core.config import settings


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    return SentenceTransformer(settings.embedding_model, device="cpu")


def embed_text(text: str) -> list[float]:
    if not text or not text.strip():
        raise ValueError("Cannot embed empty text")

    model = get_embedding_model()
    embedding = model.encode(text.strip(), normalize_embeddings=True, convert_to_numpy=True)
    return embedding.tolist()
