# PracticeLoop

A lean, all-Python spaced-practice platform for interview and skill prep. Capture a
question — typed or pasted and AI-structured — find it again by meaning with semantic
search, type an answer and have it graded rather than guessing your own score, get
scheduled a review date, and keep a streak.

**Live: [practiceloop.onrender.com](https://practiceloop.onrender.com)**

> Hosted on Render's free tier, so the first request after idle time can take ~50s to wake
> the instance back up — that's the platform spinning down, not the app being slow.

## What it does

- **Capture** a question by typing it, pasting free-form notes (an LLM structures it into
  fields; a deterministic marker-based fallback works with no LLM key at all), or letting AI
  generate a fresh one for a topic with no local match yet.
- **Find it again by meaning**, not just keyword — pgvector semantic search with a real "no
  match" state instead of always returning something plausible-looking but wrong.
- **Review one card at a time.** Type your answer and an LLM grades it 1–5 against the
  stored answer with feedback on what you missed — a self-rating is unreliable input to a
  scheduler, since it's easy to rate yourself 5 on something you can't actually explain.
  Falls back to self-rating with no LLM key configured, or for a question with no stored
  answer to grade against. Either way, a spaced-repetition interval follows, plus a streak
  counter and per-topic mastery on the dashboard.
- **Edit, delete, and organize** your own question bank; every write is scoped to the signed-in
  user.
- **Start from an 80+ question starter deck** on signup instead of a blank slate — DSA,
  operating systems, computer networks, DBMS, OOP, Python, SQL, and system design, the
  breadth of a CS-fundamentals interview loop rather than one narrow slice of it.
- **Discover jobs on a schedule** against your profile's target role (Adzuna's API, no
  scraping), scored by keyword fit against your resume, and **track applications** through a
  funnel with follow-up and stale-application reminders. Applying itself stays a
  human-in-the-browser action on purpose — see [Design notes](#design-notes).
- **Diff a job description against what you actually know** — paste a JD and it's split
  into skills you've proven (on your resume and recalled), skills you've only claimed (on
  your resume, never practiced or weakly recalled), and skills you're missing outright. The
  middle bucket is the one nothing else can produce, because nothing else holds both your
  resume and your real practice history.
- **Turn a diagnosis into a deck** — generate a practice question for each missing or
  untested skill with one click, deduplicated against your existing bank so a similar JD
  doesn't regenerate cards you already have.
- **Tailor your resume to that JD** — an LLM reorders and reframes your *real* resume content
  around what the job description actually wants (a rewritten summary, reordered bullets,
  what to emphasize), and never fabricates an employer, title, or skill that isn't already
  there. No LLM key configured? Falls back to the raw keyword-overlap diff instead of a
  dead end.
- **Countdown to a tracked interview** on the dashboard, with a company-specific deck (every
  question tied to that listing's gap-analyzed skills, regardless of when it's normally due)
  and a post-interview debrief that turns what was actually asked into new practice cards —
  the highest-signal questions in the whole bank.
- **Market trends** aggregated across every listing discovered on the instance (not just your
  own) — the entry point if you have no applications yet: see what's actually in demand,
  honest about sample size, before running a gap analysis against it.

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
sample deck" for 80+ ready-to-review questions), and go.

**Without Docker:** requires Python 3.10+ and a local or remote Postgres with the `pgvector`
extension available (`CREATE EXTENSION vector` must succeed).

```bash
git clone https://github.com/SAYUSHMAN18/PracticeLoop.git && cd PracticeLoop
cp .env.example .env            # fill in GROQ_API_KEY, DATABASE_URL

python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

pip install -e ".[dev,groq]"    # swap `groq` for `gemini` or `bedrock` if using those
docker compose up -d db         # or point DATABASE_URL at your own Postgres
python scripts/migrate.py       # applies migrations/*.sql -- no psql required
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

For scheduled job discovery on your fork: set `JOBS_CRON_TOKEN` on the Render service (any
random string) and add the same value as a `JOBS_CRON_TOKEN` secret in your fork's GitHub repo
settings (**Settings → Secrets and variables → Actions**) -- that's what
`.github/workflows/jobs-discover.yml` authenticates with. Add `ADZUNA_APP_ID`/`ADZUNA_APP_KEY`
(free at [developer.adzuna.com](https://developer.adzuna.com)) to actually find listings;
without them, discovery runs happen and get recorded but find nothing.

## What's here vs. what's deferred

Built: capture (manual or AI-structured from pasted text, with a deterministic marker-based
fallback if no LLM key is configured), pgvector semantic search with a real "no match" state,
LLM-graded review (self-rating as the fallback) with spaced repetition and streaks, per-topic
mastery on the dashboard, question editing and deletion, an 80+ question CS-fundamentals
starter deck, and AI
study-card generation for a topic with no local match. Scheduled job discovery (GitHub Actions
cron, since the free tier has none of its own) with keyword-based fit scoring, a real
application tracker with funnel stats, JD-vs-actual-recall skill gap analysis, one-click deck
generation from the gaps, interview countdowns with company-specific decks and post-interview
debriefs, and a market-trends scanner aggregated across every discovered listing. Production
hardening: per-IP rate limiting, a per-user daily LLM budget, security headers, request size
caps, a non-root Docker image, versioned migrations, CI, and a health-checked deploy.

Deliberately not built yet: LLM-scored job fit (keyword scoring is the whole thing there
today, unlike gap analysis's skill extraction), PDF export, an ease-factor-based scheduler
(see [`docs/spaced-repetition.md`](docs/spaced-repetition.md) for the current algorithm's
limits), and any multi-tenant/multi-student support. Also deliberately never built: submitting
job applications on your behalf, or automating LinkedIn/Naukri from the server — both platforms'
terms bar it, and it would mean risking your account to save you a click.

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
