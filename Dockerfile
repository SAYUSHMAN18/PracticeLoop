FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
COPY migrations ./migrations
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

# Free-tier instances give this app very little headroom (512MB RAM) for a
# process that imports torch/transformers -- these cap each library's own
# thread-pool buffer allocation, which is otherwise sized off the host's
# full CPU count regardless of how little of it the container is allotted.
ENV OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    TOKENIZERS_PARALLELISM=false

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", 8000)}/healthz', timeout=3)" || exit 1

# PORT is honored so this image runs unmodified on host platforms that assign
# it dynamically (e.g. Render); it falls back to 8000 for docker-compose/bare
# `docker run`. migrate.py only applies migrations not yet recorded in
# schema_migrations, so running it on every container start is safe and
# keeps schema setup identical across every deploy path -- first boot on a
# fresh database, a redeploy of a live one, docker-compose, or bare `docker run`.
CMD python scripts/migrate.py \
    && python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1}
