# How PracticeLoop schedules reviews

## The idea

Spaced repetition pushes a review further into the future every time you show you know
something, and pulls it back to "soon" the moment you don't. The goal is to review each
item just as your memory of it is about to fade — not sooner (wasted effort) and not later
(you've already forgotten).

## What this app uses: FSRS

PracticeLoop schedules reviews with [FSRS](https://github.com/open-spaced-repetition) (Free
Spaced Repetition Scheduler) via the `fsrs` package, wired up in
[`app/practice/fsrs_scheduler.py`](../app/practice/fsrs_scheduler.py).

FSRS models each card with three numbers it updates on every review:

- **stability** — how many days until recall probability drops to 90%. Grows each time you
  successfully recall; the interval to the next review is derived from it.
- **difficulty** — how hard *this specific card* is for you (0–10). Nudged up on a lapse,
  down on an easy recall. This is what an ease-factor-based scheduler like SM-2 approximates
  with a single per-card multiplier; FSRS separates it from stability.
- **retrievability** — the current estimated probability you'd recall the card right now
  (0–1), computed from stability and time since last review.

A card's memory state lives in the `card_states` table, one row per question, upserted on
every attempt. A question with no `card_states` row (or a null `stability`) is treated as a
brand-new card — no guessing at a memory state that doesn't exist.

## Rating scale mapping

The app's self-rating and AI-grading scales are both 1–5 ("1 — blackout" through "5 —
easy"). FSRS's `Rating` has four values, so `_RATING_MAP` collapses them:

| App rating | FSRS rating | Meaning |
|---|---|---|
| 1 | `Again` | total blank / lapse |
| 2 | `Hard` | recalled, but a struggle |
| 3 | `Good` | passing recall |
| 4 | `Good` | solid recall |
| 5 | `Easy` | instant, effortless |

3 and 4 both map to `Good` — both are a passing recall — rather than inventing a fifth FSRS
bucket the algorithm doesn't have. Auto-graded multiple-choice answers land one notch inside
the extremes: **4 (`Good`)** for correct, **2 (`Hard`)** for incorrect, since a right or
wrong click doesn't carry the certainty of a self-assessed "I knew this cold" or "I drew a
total blank" (see `record_mcq_attempt` in `app/practice/service.py`).

## No same-session relearning steps

```python
_SCHEDULER = fsrs.Scheduler(learning_steps=(), relearning_steps=())
```

FSRS's built-in (re)learning steps exist for Anki-style intra-session drilling — a brand-new
or just-lapsed card comes back a few minutes later, same sitting. This app reviews on a
day-by-day cadence only ("come back tomorrow", "in a week"), so those steps are disabled:
every card is scheduled to a real future review day, and a lapsed card is due again the next
day rather than 10 minutes later.

## What "due" means

`due_for_review` (in `app/practice/service.py`) selects every question where
`card_states.due` is null (never reviewed — due immediately) or `due::date <= today`,
ordered by due date with never-reviewed cards first. That one query is the review queue, the
"just review" count on the dashboard, and the base of the adaptive daily plan.

## Retrievability drives more than the queue

`retrievability_bulk` computes FSRS's current recall estimate for every one of a user's
reviewed cards in a single query. Phase 5.4's per-topic **mastery score** blends that
estimate (weighted 40%) with recency-weighted, difficulty-adjusted self-rated confidence —
so "which topics are weak" reuses the same scheduler that decides what's due, instead of
being a second disconnected opinion (see `topic_mastery` in `app/dashboard/service.py`).

## Why FSRS instead of a hand-rolled SM-2

An earlier version of this app used a simplified SM-2 variant: fixed interval multipliers
keyed by rating, no per-card ease factor, no interval ceiling. That had a known failure mode
— a long streak of "easy" ratings pushed a card's interval out with no cap (14 → 35 → 88 →
219 → 548 days). FSRS is a well-studied model fit against millions of real reviews, handles
the ease/stability split properly, and manages interval growth without an ad-hoc ceiling.
Adopting the maintained `fsrs` package also means the scheduling logic isn't ours to keep
tuning by hand.
