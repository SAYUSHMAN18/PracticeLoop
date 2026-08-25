# ADR 001: PracticeLoop's lineage and why it's a new, lean repo

## Context
Two existing personal projects overlap in this space:

- **PrepGuru** — Streamlit + Supabase interview-prep tool. Capture a Q&A (originally via a
  Telegram bot), structure it with an LLM, embed it (`sentence-transformers` + pgvector)
  for semantic search, self-rate practice attempts, export a study PDF. Real mechanic,
  small/standalone footprint.
- **CareerOS** — a multi-tenant career-intelligence platform (student profiles, resume
  parsing, job matching, application CRM) for colleges. Its own
  [`docs/adr/011-interview-prep-prepguru.md`](https://github.com/SAYUSHMAN18/CareerOS/blob/main/docs/adr/011-interview-prep-prepguru.md)
  already considered folding PrepGuru in directly and **rejected it**: PrepGuru's data has
  no natural tenant, and bolting on CareerOS's JWT/RBAC/row-level-security stack to serve a
  single student would be complexity with no payoff. That ADR ported the *data model* into
  a CareerOS module instead, and explicitly deferred PrepGuru's embeddings, market-trend
  scanner, streaks, and PDF export as out of scope for a tenant-scoped module.

Those deferred pieces are exactly what makes PrepGuru's practice loop good. They belong
somewhere — just not inside a multi-tenant SaaS built for institutions.

## Decision
Build PracticeLoop as a **new, single-tenant, all-Python repo**:

- **Core loop, taken from PrepGuru, finished this time**: capture a question (typed or
  pasted as raw text and LLM-structured), embed it, find it again by meaning not keyword,
  self-rate a practice attempt, get scheduled a review date, keep a streak.
- **Profile + opportunity framing, taken from CareerOS, simplified**: a lightweight student
  profile (target role, resume text) that practice content and matching can key off of —
  without CareerOS's tenancy, RBAC, or RLS, because there is exactly one tenant: the
  student using the app.
- **LLM provider swap**: the same pattern already proven in `nl2sql/app/core/llm.py` — one
  `generate()` entrypoint, `LLM_PROVIDER=groq|gemini|bedrock` picks the backend.
- **No JS build.** FastAPI + Jinja2 + HTMX. Interactivity (search-as-you-type, marking a
  review done, generating a study card) is server-rendered HTML fragments, not a SPA.

## What's explicitly out of scope for v1
- Multi-tenant / multi-college support — CareerOS already owns that problem.
- Telegram bot capture — a web form replaces it; the bot was a capture convenience, not
  the mechanic.
- Market-trend scanner (`market_scanner.py` in PrepGuru) — real, but a separable
  fast-follow once the core loop has real usage data to react to.
- PDF export — same: real, separable, not blocking.

## Consequences
- PrepGuru and CareerOS both continue to exist unchanged; this repo doesn't fold either
  one in, it starts fresh with their validated ideas.
- Because there's no tenancy layer, the data model is a fraction of CareerOS's size —
  faster to build, faster to read.
- If this later needs to serve multiple students under one deployment with real isolation,
  that's the same fork-in-the-road CareerOS's ADR 011 already hit — cross that bridge with
  the same answer: don't retrofit tenancy onto a system that was never scoped for it,
  design it in from a schema migration, same as CareerOS did.
