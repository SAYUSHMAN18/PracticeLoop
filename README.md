# PracticeLoop

A lean, all-Python spaced-practice platform for interview/skill prep. Capture a question
(typed or pasted and AI-structured), find it again by meaning via semantic search, self-rate
a practice attempt, get scheduled a review date, keep a streak.

See [`docs/adr/001-lineage-and-scope.md`](docs/adr/001-lineage-and-scope.md) for why this is
a new repo rather than a merge into [CareerOS](https://github.com/SAYUSHMAN18/CareerOS) or
[PrepGuru](https://github.com/SAYUSHMAN18/PrepGuru), and what it deliberately takes from each.
See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the request flow and data model.

## Stack

FastAPI + Jinja2 + HTMX (no JS build step) · asyncpg + pgvector · sentence-transformers
local embeddings · swappable LLM provider (`LLM_PROVIDER=groq|gemini|bedrock`, same pattern
as [nl2sql](https://github.com/SAYUSHMAN18/NL2SQL)'s `app/core/llm.py`).

## First run (Docker, one command)

```bash
git clone https://github.com/SAYUSHMAN18/PracticeLoop.git && cd PracticeLoop
cp .env.example .env            # fill in GROQ_API_KEY (or GEMINI_API_KEY)
docker compose up
```

This builds the app image, waits for Postgres to actually accept connections, applies the
schema, and starts the server. Visit `http://localhost:8000`, sign up (check "start with a
sample deck" for 30+ ready-to-review questions), and go.

## First run (without Docker)

Requires Python 3.10+ and a local or remote Postgres with the `pgvector` extension
available (`CREATE EXTENSION vector` must succeed).

```bash
git clone https://github.com/SAYUSHMAN18/PracticeLoop.git && cd PracticeLoop
cp .env.example .env            # fill in GROQ_API_KEY, DATABASE_URL

python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

pip install -e ".[dev,groq]"    # swap `groq` for `gemini` or `bedrock` if using those
docker compose up -d db         # or point DATABASE_URL at your own Postgres
python scripts/init_db.py       # applies scripts/schema.sql -- no psql required
uvicorn app.main:app --reload
```

Or, once the venv exists: `make setup && make db && make dev` (see the `Makefile`).

## Load the starter deck for an existing user

The signup form offers this automatically. To load it for a user after the fact:

```bash
python scripts/seed.py you@example.com
```

## Tests

```bash
pytest tests/ -q
```

## What's here vs. what's deferred

Built: capture (manual or AI-structured from pasted text, with a deterministic marker-based
fallback if no LLM key is configured), pgvector semantic search with a real "no match"
state, a one-card-at-a-time review queue with spaced repetition and streaks, per-topic
mastery on the dashboard, question editing and deletion, a 30+ question starter deck, and
AI study-card generation for a topic with no local match.

Deliberately not built yet: the market-trends scanner, PDF export, an ease-factor-based
scheduler (see [`docs/spaced-repetition.md`](docs/spaced-repetition.md) for the current
algorithm's limits), database migrations (schema changes require a fresh database today),
and any multi-tenant/multi-student support (see the ADR).
