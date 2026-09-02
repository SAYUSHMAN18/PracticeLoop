# PracticeLoop

**Turn a goal into a study plan, learn the material, and prove you actually retained it —
on one account, one practice history, one spaced-repetition engine.**

Most study apps are a flashcard box with a timer bolted on. PracticeLoop is the whole loop:
describe what you're working toward, get a real module → unit → lesson path back, work
through lesson content and quizzes, get measured by a diagnostic instead of a self-rating,
and come back to review exactly what you're about to forget. An AI mentor, Math and Writing
labs, hands-on projects with a portfolio, and a full interview/job-search toolkit all sit on
top of the same engine.

**Live demo: [practiceloop.onrender.com](https://practiceloop.onrender.com)**

> Hosted on Render's free tier — the first request after ~15 min idle takes ~50s while the
> instance wakes up. That's the platform spinning down, not the app.

---

## For students: what a first session looks like

1. **Sign up** and tick *"start with a sample deck"* — you get 80+ ready-to-review
   CS-fundamentals questions so nothing is empty on day one.
2. **Tell it your goal** on the welcome screen ("prepare for my data structures final",
   "get job-ready in Python"). Skipping is fine; you can set it later.
3. **Start a learning path** — type the goal or pick a subject template (Python, algebra,
   NEET biology, personal finance, English speaking, Class 8 science). You get a real
   structured path; open any lesson for a concept explanation, a worked example, a
   checkpoint question, and a summary.
4. **Take a diagnostic** on any topic — a scored multiple-choice quiz that tells you which
   subtopics are weak, instead of asking you to guess "beginner / intermediate / advanced".
   Then hit **"Build my focus module"** and those exact gaps become lessons at the top of
   your path.
5. **Do today's plan** — everything due for review, plus a pick from your weakest topic and
   a harder question, each labelled with why it's there.
6. **Ask Loop Mentor** (the panel on the right) to explain something more simply, give a
   hint, suggest a memory trick, or tell you what to study next. It knows which lesson or
   path you're looking at.

XP, streaks, levels, and badges accrue for real learning activity — finishing a lesson,
passing a diagnostic, closing a review — not for clicking around.

---

## What it does

### Learn
- **Goal → learning path.** A real module → unit → lesson structure from a typed goal or a
  subject template. An LLM generates it when one's configured; a deterministic skeleton
  generator produces a usable structure either way — never an empty state.
- **Real lesson content**, generated once and cached: concept, worked example, checkpoint
  question with reveal-to-check answer, summary.
- **One review queue for every format.** Multiple-choice concept checks and typed free-text
  recall are scheduled by the same FSRS engine and compete for review time on their real due
  dates — not two separate systems.
- **A real diagnostic** on any topic: an AI-generated MCQ quiz, scored instantly, mapped to
  a proficiency level, with the weak subtopics named — and **one click turns those gaps into
  lessons**, as a focus module inserted at the *top* of a learning path (existing or new),
  so the next thing you study is the thing just measured as weakest.
- **Quiz Arena** — a timed round mixing free-text and MCQ questions from your whole bank.
- **Loop Mentor** — a context-aware AI tutor. No provider configured, or the daily budget
  spent? It says so honestly instead of faking a reply or crashing.
- **Two subject labs.** *Math Lab* solves and verifies single-variable equations with
  sandboxed symbolic evaluation (`sympy`, not a bare `eval`) plus optional AI step-by-step
  explanations. *Writing Lab* scores pasted essay / cover-letter / short-answer text on
  clarity, structure, and grammar (AI-backed; see *degrade honestly* below).
- **Projects** — an AI-suggested idea and milestone list for any topic (deterministic
  fallback if no LLM), XP per milestone and submission, optional AI feedback, and a
  shareable **portfolio page** aggregating every submitted project and earned badge.
- **Progress you can trust** — per-topic retrievability from the same FSRS scheduler that
  drives review, a learning timeline, and a plain-language recommendation built from your
  real numbers.

### Teach and support
- **Classrooms** (teacher role): create one with a join code, see a roster, post
  assignments that notify every member.
- **Guardian links**: a student generates an invite, a parent accepts it, and from then on
  the guardian sees a *summary* (streak, level, XP, paths completed) — never raw content
  like mentor chats or diagnostic detail. No role grants cross-user access by itself; every
  link is an explicit join code or an explicitly accepted invite.

### Job-search prep (the app's original scope, still fully intact)
- **Capture a question** by typing it, pasting free-form notes (LLM-structured, with a
  deterministic marker-based fallback), or generating one for a topic — then **find it again
  by meaning** with pgvector semantic search.
- **Honest grading** — an LLM scores a typed answer 1–5 against the stored answer with
  feedback on what was missed; self-rating is the explicit fallback when no LLM is
  configured.
- **Job discovery on a schedule**, scored by resume fit, with an **application tracker**
  funnel and follow-up reminders.
- **Skill-gap analysis** — diff a job description against what you've *proven* (real recall
  history), what you've only *claimed* (resume), and what's missing — then generate practice
  cards for the gaps in one click, and **tailor your resume** to that JD without fabricating
  experience.

### Account and platform
- **Export all your data** as one JSON file, or **permanently delete your account** (with
  password re-confirmation) — every user-referencing table cascades, so deletion is complete.
- **Installable as a PWA** — manifest, icon, and a service worker that caches the static
  shell and serves an honest offline page (no pretending a server-rendered app works fully
  offline).
- Uploads are checked against their real file signature before being stored or parsed;
  responses carry a real CSP; per-IP rate limiting, a per-user daily LLM budget, security
  headers, and request-size caps apply platform-wide.

---

## Stack

FastAPI + Jinja2 + HTMX (no JS build step) · asyncpg + pgvector · `sentence-transformers`
local embeddings (CPU-only, no API key) · [FSRS](https://github.com/open-spaced-repetition)
for spaced repetition · `sympy` for sandboxed equation solving · swappable LLM provider
(`LLM_PROVIDER=groq|gemini|bedrock`).

Every AI-backed feature degrades to a deterministic fallback or an honest "unavailable"
message when no provider is configured — never a fake result.

---

## Run it locally

### Docker (one command)

```bash
git clone https://github.com/SAYUSHMAN18/PracticeLoop.git && cd PracticeLoop
cp .env.example .env            # add GROQ_API_KEY (or GEMINI_API_KEY) — optional
docker compose up
```

Builds the image, waits for Postgres, applies migrations, starts the server. Open
`http://localhost:8000`, sign up, done.

### Without Docker

Requires Python 3.10+ and a Postgres with `pgvector` available (`CREATE EXTENSION vector`
must succeed).

```bash
git clone https://github.com/SAYUSHMAN18/PracticeLoop.git && cd PracticeLoop
cp .env.example .env            # fill in DATABASE_URL, optionally GROQ_API_KEY

python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev,groq]"    # swap `groq` for `gemini` or `bedrock`

docker compose up -d db         # or point DATABASE_URL at your own Postgres
python scripts/migrate.py       # applies migrations/*.sql — no psql needed
uvicorn app.main:app --reload
```

Or, once the venv exists: `make setup && make db && make dev`.

**Load the starter deck for an existing user:**

```bash
python scripts/seed.py you@example.com
```

### Tests

316 tests across 46 files — spaced repetition and semantic search, every AI-backed feature's
fallback *and* configured path, ownership/IDOR checks on every cross-user-reachable route,
migration idempotency, upload validation, and more.

```bash
pytest -q
ruff check app tests scripts && ruff format --check app tests scripts
```

---

## Deploy your own copy

`render.yaml` is a ready-to-go [Render Blueprint](https://render.com/docs/blueprint-spec): a
free pgvector-capable Postgres plus a Docker web service, wired so `DATABASE_URL` is never
typed by hand.

1. Fork this repo.
2. Render dashboard → **New → Blueprint** → point it at your fork.
3. Set `GROQ_API_KEY` when prompted (get one free at
   [console.groq.com](https://console.groq.com)). Everything else has a default or is
   generated.
4. First deploy runs migrations automatically via the container's start command.

**Scheduled job discovery** (optional): set `JOBS_CRON_TOKEN` on the Render service (any
random string) and add the same value as a repo secret under **Settings → Secrets and
variables → Actions** — that's what `.github/workflows/jobs-discover.yml` authenticates
with. Add `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` (free at
[developer.adzuna.com](https://developer.adzuna.com)) to actually find listings.

See [`ARCHITECTURE.md`](ARCHITECTURE.md#production-hardening) for what else is configurable
(worker count, rate limits, security headers).

---

## Design principles

- **Degrade honestly, never fake it.** Every AI-backed feature has a real, useful path with
  no LLM at all — a deterministic path skeleton, a marker-based question parser, self-rating
  instead of LLM grading — plus a visible, truthful notice rather than a silent stub. Two
  features have no reliable non-LLM equivalent and so fail loud with the real error instead
  of guessing: **skill-gap extraction** from a pasted JD, and **Writing Lab** feedback.
- **Multi-tenancy without implicit trust.** A teacher or guardian role never grants access
  to another person's data by itself. Every cross-user view needs an explicit action from
  the data's owner, and even then a guardian sees only a summary.
- **Spaced repetition is the spine.** MCQs, free-text recall, and diagnostics all schedule
  through the same FSRS engine, so "what's due today" is one honest answer across every
  format.
- **Deletion means deletion.** Every user-referencing table uses `ON DELETE CASCADE`,
  verified across the whole schema — account deletion is one query and it's complete.

---

## Deliberately not built

Full offline functionality (this is real round trips, not a bundled SPA); a community /
study-groups layer beyond classrooms and guardian links; malware scanning for uploads
(signature validation catches spoofed types; a real AV engine is a separate integration);
and — never — submitting job applications on your behalf or automating LinkedIn/Naukri from
the server. Both platforms' terms bar it, and it would mean risking your account to save a
click.

---

## Design notes

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — request flow, data model, module layout, production
  hardening.
- [`docs/spaced-repetition.md`](docs/spaced-repetition.md) — how FSRS schedules reviews here
  and why.
- [`docs/semantic-search.md`](docs/semantic-search.md) — what an embedding is, why pgvector,
  and a concrete example keyword search would miss.
- [`docs/adr/001-lineage-and-scope.md`](docs/adr/001-lineage-and-scope.md) — why this is a
  standalone project rather than a feature bolted onto an existing one.

## License

MIT — see [`LICENSE`](LICENSE).
