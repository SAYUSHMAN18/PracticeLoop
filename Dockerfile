FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
COPY data ./data

# The default torch wheel drags in full CUDA (nvidia-cudnn, triton, cuda-toolkit...)
# even though inference here only ever runs on CPU -- that bloats the image by
# ~2GB and was enough extra baseline memory to OOM-kill the container on a small
# free-tier instance the moment the embedding model loaded. Installing the CPU-only
# wheel first satisfies sentence-transformers' torch requirement without pip ever
# reaching for the CUDA build.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -e ".[groq]" \
    && useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", 8000)}/healthz', timeout=3)" || exit 1

# PORT is honored so this image runs unmodified on host platforms that assign
# it dynamically (e.g. Render); it falls back to 8000 for docker-compose/bare
# `docker run`. init_db.py is idempotent (CREATE ... IF NOT EXISTS), so
# running it on every container start is safe and keeps schema setup the
# same regardless of which platform the image is deployed to.
CMD python scripts/init_db.py \
    && python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1}
