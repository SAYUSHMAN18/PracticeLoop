# PracticeLoop

A lean, all-Python spaced-practice platform for interview and skill prep. Capture a
question — typed or pasted and AI-structured — find it again by meaning with semantic
search, self-rate a practice attempt, get scheduled a review date, and keep a streak.

**Live: [practiceloop.onrender.com](https://practiceloop.onrender.com)**

> Hosted on Render's free tier, so the first request after idle time can take ~50s to wake
> the instance back up — that's the platform spinning down, not the app being slow.

## What it does

- **Capture** a question by typing it, pasting free-form notes (an LLM structures it into
  fields; a deterministic marker-based fallback works with no LLM key at all), or letting AI
  generate a fresh one for a topic with no local match yet.
- **Find it again by meaning**, not just keyword — pgvector semantic search with a real "no
  match" state instead of always returning something plausible-looking but wrong.
- **Review one card at a time**, self-rate 1–5, and get a spaced-repetition interval based on
  how it actually went — plus a streak counter and per-topic mastery on the dashboard.
- **Edit, delete, and organize** your own question bank; every write is scoped to the signed-in
  user.
- **Start from a 30+ question starter deck** on signup instead of a blank slate.

## Stack

FastAPI + Jinja2 + HTMX (no JS build step) · asyncpg + pgvector · sentence-transformers local
embeddings, CPU-only · swappable LLM provider (`LLM_PROVIDER=groq|gemini|bedrock`).

## Run it locally

**Docker, one command:**

```bash
git clone https://github.com/SAYUSHMAN18/PracticeLoop.git && cd PracticeLoop
cp .env.example .env            # fill in GROQ_API_KEY (or GEMINI_API_KEY)
docker compose up
```

This builds the app image, waits for Postgres to actually accept connections, applies the
schema, and starts the server. Visit `http://localhost:8000`, sign up (check "start with a
sample deck" for 30+ ready-to-review questions), and go.

**Without Docker:** requires Python 3.10+ and a local or remote Postgres with the `pgvector`
extension available (`CREATE EXTENSION vector` must succeed).

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

**Load the starter deck for an existing user** (the signup form offers this automatically;
to load it for a user after the fact):

```bash
python scripts/seed.py you@example.com
```

**Tests:**

```bash
pytest tests/ -q
```

## Deploying your own copy

`render.yaml` is a ready-to-go [Render Blueprint](https://render.com/docs/blueprint-spec): a
free Postgres instance (pgvector-capable) plus a Docker web service, wired together so
`DATABASE_URL` never needs typing in by hand. On Render's dashboard: **New → Blueprint**,
point it at your fork, and set `GROQ_API_KEY` when prompted. See
[`ARCHITECTURE.md`](ARCHITECTURE.md#production-hardening) for what else is configurable
(worker count, rate limits, security headers).

## What's here vs. what's deferred

Built: capture (manual or AI-structured from pasted text, with a deterministic marker-based
fallback if no LLM key is configured), pgvector semantic search with a real "no match" state,
a one-card-at-a-time review queue with spaced repetition and streaks, per-topic mastery on the
dashboard, question editing and deletion, a 30+ question starter deck, and AI study-card
generation for a topic with no local match. Production hardening: per-IP rate limiting,
security headers, request size caps, a non-root Docker image, and a health-checked deploy.

Deliberately not built yet: the market-trends scanner, PDF export, an ease-factor-based
scheduler (see [`docs/spaced-repetition.md`](docs/spaced-repetition.md) for the current
algorithm's limits), database migrations (schema changes require a fresh database today), and
any multi-tenant/multi-student support.

## Design notes

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — request flow, data model, module layout, production
  hardening.
- [`docs/spaced-repetition.md`](docs/spaced-repetition.md) — how the review scheduling
  algorithm works and why.
- [`docs/semantic-search.md`](docs/semantic-search.md) — what an embedding is, why pgvector,
  and a concrete example a keyword search would miss.
- [`docs/adr/001-lineage-and-scope.md`](docs/adr/001-lineage-and-scope.md) — why this is a
  standalone project rather than a feature bolted onto an existing one, and what it
  deliberately takes cues from.

## License

MIT — see [`LICENSE`](LICENSE).
