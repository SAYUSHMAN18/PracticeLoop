# PracticeLoop

An adaptive learning platform, not just a flashcard app: turn a goal into a structured
learning path, work through real lesson content and quizzes, get measured by an actual
diagnostic instead of a self-reported skill level, and practice on a schedule that adapts
to what you're forgetting. An AI mentor, subject labs, hands-on projects, and the original
interview-prep toolkit (resume tailoring, job tracking, skill-gap analysis) all sit on the
same account, the same practice history, and the same spaced-repetition engine.

**Live: [practiceloop.onrender.com](https://practiceloop.onrender.com)**

> Hosted on Render's free tier, so the first request after idle time can take ~50s to wake
> the instance back up — that's the platform spinning down, not the app being slow.

## What it does

**Learn**
- **Turn a goal into a learning path** — describe what you're working toward (or pick a
  ready-made subject template: Python, algebra, NEET biology, personal finance, and more)
  and get a real module → unit → lesson structure back. An LLM generates it when one's
  configured; a deterministic skeleton generator produces a genuinely useful structure
  either way, not an empty state.
- **Work through real lesson content**, not just a title — every lesson expands into a
  concept explanation, a worked example, a checkpoint question with a reveal-to-check
  answer, and a summary, generated once and cached rather than regenerated on every visit.
- **Practice with MCQs and free-text recall in the same review queue**, both scheduled by
  the same spaced-repetition engine, so a multiple-choice concept check and a typed-answer
  interview question compete for review time on their actual due dates, not two separate
  systems.
- **Take a real diagnostic** on any topic — an AI-generated multiple-choice quiz, scored
  instantly and mapped to a proficiency level, that tells you exactly which subtopics you're
  weak in instead of asking you to self-rate "beginner / intermediate / advanced."
- **Get XP, streaks, levels, and badges** for real learning activity — finishing a lesson,
  passing a diagnostic, closing a review — not clicks, plus a timed **Quiz Arena** mode that
  mixes free-text and MCQ questions into one fast-paced round.
- **Ask Loop Mentor**, a context-aware AI chat that knows what lesson or path you're looking
  at and can explain something more simply, give a hint, suggest a memory trick, or tell you
  what to study next. No AI configured, or budget exhausted for the day? It says so honestly
  instead of pretending or crashing.
- **Use two subject labs**: a Math Lab that solves and verifies single-variable equations
  with sandboxed symbolic evaluation (not a naive `eval`) and optional AI-generated
  step-by-step explanations, and a Writing Lab that scores a paste of essay/cover-letter/
  short-answer text on clarity, structure, and grammar with concrete feedback.
- **Build a project and submit it for feedback** — an AI-suggested idea and milestone list
  for any topic (or a deterministic fallback), XP for each milestone and each submission,
  optional AI feedback on what you turn in, and a **portfolio page** that aggregates every
  submitted project and earned badge in one shareable view.
- **See your real retention and progress** — per-topic retrievability from the same FSRS
  scheduler that drives review, a learning timeline, and a plain-language recommendation
  built from your actual numbers, not a canned tip.

**Teach and support**
- **Run a classroom** (teacher role): create one with a join code, see a roster, and post
  assignments that notify every member. **Guardian links** work the opposite way — a
  student generates an invite link, a parent accepts it, and from then on the guardian sees
  a summary (streak, level, XP, paths completed) — never raw content like mentor chats or
  diagnostic detail. Nobody gets cross-user access just by having a role; every link is
  either an explicit join code or an explicit accepted invite.

**Prep for the job search** (the app's original scope, still fully intact)
- **Capture a question** by typing it, pasting free-form notes (LLM-structured, with a
  deterministic marker-based fallback), or letting AI generate one for a topic with no
  local match yet, then **find it again by meaning** with pgvector semantic search.
- **Review with honest grading** — an LLM scores a typed answer 1–5 against the stored
  answer with feedback on what was missed, never a self-rating pretending to be measurement,
  with self-rating as the explicit fallback when no LLM is configured.
- **Discover jobs on a schedule**, score them by resume fit, and **track applications**
  through a funnel with follow-up reminders.
- **Diff a job description against what you actually know** — skills you've proven, skills
  you've only claimed on your resume, and skills you're missing outright — then generate
  practice cards for the gaps in one click and **tailor your resume** to that JD without
  ever fabricating experience.

**Account and platform**
- **Export all your data** as one JSON file, or **permanently delete your account** (with
  password re-confirmation) — every table that references a user cascades, so deletion is
  actually complete, not a partial wipe.
- **Installable as a PWA** — a manifest, an app icon, and a service worker that caches the
  static shell and serves an honest offline page when there's no connection, without
  pretending a server-rendered app can work fully offline.
- Uploads are checked against their real file signature (not just the extension) before
  being stored or parsed, responses carry a real Content-Security-Policy, and every other
  hardening layer from the original scope — rate limiting, a per-user daily LLM budget,
  security headers, request size caps — still applies platform-wide.

## Stack

FastAPI + Jinja2 + HTMX (no JS build step) · asyncpg + pgvector · sentence-transformers
local embeddings, CPU-only · FSRS for spaced repetition · sympy for sandboxed equation
solving · swappable LLM provider (`LLM_PROVIDER=groq|gemini|bedrock`), with every AI-backed
feature degrading to a deterministic fallback or an honest "unavailable" message when no
provider is configured, never a fake result.

## Run it locally

**Docker, one command:**

```bash
git clone https://github.com/SAYUSHMAN18/PracticeLoop.git && cd PracticeLoop
cp .env.example .env            # fill in GROQ_API_KEY (or GEMINI_API_KEY)
docker compose up
```

This builds the app image, waits for Postgres to actually accept connections, applies the
schema, and starts the server. Visit `http://localhost:8000`, sign up (check "start with a
sample deck" for 80+ ready-to-review CS-fundamentals questions), and go.

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

**Tests:** 300 tests across 47 files — spaced repetition and semantic search, every
AI-backed feature's fallback and configured path, ownership/IDOR checks on every
cross-user-reachable route, migration idempotency, and more.

```bash
pytest -q
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

## Design principles

A few decisions repeat across every phase of this app, worth naming once instead of per
feature:

- **Degrade honestly, never fake it.** Every AI-backed feature has a real, useful path with
  no LLM configured at all — a deterministic learning-path skeleton, a marker-based question
  parser, self-rating instead of LLM grading — and a visible, truthful notice rather than a
  silent stub or a made-up result. The one exception is skill-gap extraction from a pasted
  job description, which has no reliable non-LLM equivalent; that one fails loud with the
  real error instead of guessing.
- **Multi-tenancy without implicit trust.** A teacher or guardian role never grants access to
  another person's data by itself. Every cross-user view requires an explicit action from the
  data's owner — a join code shared on purpose, an invite link accepted on purpose — and even
  then a guardian sees a summary (streak, XP, paths completed), never raw content like mentor
  conversations or diagnostic detail.
- **Spaced repetition is the spine, not one feature among many.** MCQs, free-text recall
  questions, and diagnostics all schedule through the same FSRS-based engine, so "what's due
  today" is one honest answer across every practice format instead of a different queue per
  feature.
- **Deletion means deletion.** Every table that references a user does so with
  `ON DELETE CASCADE`, verified across the whole schema — account deletion is one query, and
  it's actually complete.

## What's here vs. what's deferred

Sixteen build phases, roughly in order: the original capture/review/jobs toolkit; a
three-panel app shell with a command palette and real accessibility support (skip links,
ARIA-live regions, a dyslexia-friendly font option, a WCAG-verified high-contrast theme);
goal-to-path learning paths with subject templates; interactive lesson content and MCQ
questions folded into the same review queue; a real diagnostic assessment; XP/levels/badges
and Quiz Arena; Loop Mentor's context-aware AI chat; Math and Writing labs; projects with a
public portfolio; teacher classrooms and consent-gated guardian access; retention analytics
and notifications; and account data export/deletion, upload content validation, a CSP, and
PWA installability. Production hardening throughout: per-IP rate limiting, a per-user daily
LLM budget, security headers, request size caps, a non-root Docker image, versioned
migrations, CI, and a health-checked deploy.

Deliberately not built: an ease-factor-based scheduler as an alternative to FSRS (see
[`docs/spaced-repetition.md`](docs/spaced-repetition.md) for the current algorithm), a
community/study-groups layer beyond classrooms and guardian links, malware-scanning for
uploads (content-signature validation catches spoofed file types; a real AV engine is a
separate integration this deploy doesn't have), and full offline functionality for a
server-rendered app that's architecturally a series of real round trips, not a bundled SPA.
Also deliberately never built: submitting job applications on your behalf, or automating
LinkedIn/Naukri from the server — both platforms' terms bar it, and it would mean risking
your account to save you a click.

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
