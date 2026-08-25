# Architecture

## Request path: capturing and reviewing a question

```mermaid
flowchart LR
    subgraph Capture
        A[Paste raw text] --> B[LLM structures it<br/>into fields]
        B -->|LLM unavailable| C[Deterministic Q:/A:<br/>marker fallback]
        B --> D[embed_text_async]
        C --> D
        D --> E[(questions table<br/>+ pgvector embedding)]
    end

    subgraph Search
        F[Type a query] --> G[embed_text_async]
        G --> H["cosine distance <=><br/>WHERE distance < 0.65"]
        H --> E
    end

    subgraph Review
        I[GET /practice/review] --> J[due_for_review:<br/>next_review_at <= today<br/>OR never attempted]
        J --> E
        K[Rate 1-5] --> L[record_attempt]
        L --> M[spaced_repetition.next_review_date]
        M --> N[(attempts table)]
        L --> O[HTMX swaps in<br/>next card]
    end
```

## Module layout

Each feature is a self-contained package under `app/`: `auth`, `profile`, `practice`,
`dashboard`. Every one follows the same shape:

- `router.py` — FastAPI routes. Thin: parses the request, calls `service.py`, picks a
  template. No SQL, no business logic here.
- `service.py` — the actual logic and SQL. Framework-agnostic; could be called from a CLI
  script (see `scripts/seed.py`) without touching FastAPI at all.
- `extraction.py`, `spaced_repetition.py` (in `practice/`) — pure functions with no I/O,
  which is exactly why they're the only things with unit tests today (see
  [`tests/`](tests/)). `spaced_repetition.next_interval_days` in particular takes a rating
  and a previous interval and returns a new interval — no database, no clock dependency
  beyond an injectable `today` parameter.

`app/core/` holds cross-cutting concerns every feature package depends on but none of them
own: `config.py` (settings), `db.py` (the asyncpg pool), `security.py` (password hashing,
sessions), `llm.py` (the provider-swap LLM client), `embedder.py` (sentence-transformers),
`templates.py` (one shared Jinja environment), `logging.py`.

This is a deliberately flat "modular monolith" shape, not a hard rule about layers — the
point is that `practice/service.py` has no idea FastAPI exists, so it's testable and
reusable on its own.

## Data model

```mermaid
erDiagram
    users ||--|| profiles : has
    users ||--o{ questions : owns
    users ||--o{ attempts : owns
    questions ||--o{ attempts : "practiced via"

    users {
        int user_id PK
        text email
        text password_hash
        text name
    }
    profiles {
        int user_id PK "FK to users, 1:1"
        text target_role
        text target_companies
        text resume_text
    }
    questions {
        int question_id PK
        int user_id FK
        text question
        text answer
        text topic
        text difficulty
        text source "manual | ai_generated | starter_deck"
        vector embedding "384-dim, sentence-transformers"
    }
    attempts {
        int attempt_id PK
        int question_id FK
        int user_id FK
        smallint confidence_rating "1-5"
        date next_review_at
        timestamptz practiced_at
    }
```

`attempts.user_id` is a deliberate denormalization (it's derivable via `questions.user_id`)
-- every ownership check and every query that scopes by user filters `attempts` directly by
it, rather than joining through `questions` every time. It's also what closes the ownership
bug documented in the code review this file exists in response to: without it, there was no
cheap way to verify a review-rating request actually belongs to the question's owner.

## Why no multi-tenancy

This schema has one row per user, not per (tenant, user) -- see
[`docs/adr/001-lineage-and-scope.md`](docs/adr/001-lineage-and-scope.md) for why that's a
deliberate choice rather than an oversight.

## Further reading

- [`docs/spaced-repetition.md`](docs/spaced-repetition.md) -- how the review scheduling
  algorithm works and why.
- [`docs/semantic-search.md`](docs/semantic-search.md) -- what an embedding is and why
  pgvector, with a concrete example of a query keyword search would miss.
