# PracticeLoop

A lean, all-Python spaced-practice platform for interview/skill prep. Capture a question
(typed or pasted and AI-structured), find it again by meaning via semantic search, self-rate
a practice attempt, get scheduled a review date, keep a streak.

See [`docs/adr/001-lineage-and-scope.md`](docs/adr/001-lineage-and-scope.md) for why this is
a new repo rather than a merge into [CareerOS](https://github.com/SAYUSHMAN18/CareerOS) or
[PrepGuru](https://github.com/SAYUSHMAN18/PrepGuru), and what it deliberately takes from each.

## Stack

FastAPI + Jinja2 + HTMX (no JS build step) · asyncpg + pgvector · sentence-transformers
local embeddings · swappable LLM provider (`LLM_PROVIDER=groq|gemini|bedrock`, same pattern
as [nl2sql](https://github.com/SAYUSHMAN18/NL2SQL)'s `app/core/llm.py`).

## First run

```bash
git clone <this-repo> && cd practiceloop
cp .env.example .env            # fill in GROQ_API_KEY (or GEMINI_API_KEY)
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate on Unix
pip install -e ".[dev]"
docker compose up -d            # postgres + pgvector on port 5435
psql "$DATABASE_URL" -f scripts/schema.sql
uvicorn app.main:app --reload
```

Visit `http://localhost:8000`, sign up, and start capturing questions.

## Tests

```bash
pytest tests/ -q
```

## What's here vs. what's deferred

Built: capture (manual or AI-structured from pasted text), pgvector semantic search,
spaced-repetition review queue with streaks, a lightweight profile (target role + resume
text), AI study-card generation for a topic with no local match, a dashboard.

Deliberately not built yet (see the ADR): the market-trends scanner, PDF export, and any
multi-tenant/multi-student support.
