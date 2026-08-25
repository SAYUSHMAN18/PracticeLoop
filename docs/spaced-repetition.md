# How PracticeLoop schedules reviews

## The idea (SM-2, briefly)

Spaced repetition schedules a review further in the future every time you demonstrate you
know something, and resets to "soon" the moment you don't. The classic algorithm, SM-2
(used by Anki and SuperMemo), tracks a per-card "ease factor" that adjusts based on every
rating you've ever given that card, so two cards with the same rating history can still end
up on different schedules if one consistently felt harder to recall.

## What this app actually does (`app/practice/spaced_repetition.py`)

A simplified version: no per-card ease factor, just a fixed multiplier keyed by the rating
(1-5) you give after seeing the answer.

```python
_FIRST_INTERVAL_DAYS = {3: 3, 4: 7, 5: 14}
_GROWTH_MULTIPLIER = {3: 1.3, 4: 1.8, 5: 2.5}
```

- **Rating 1 or 2 ("blackout" or "hard, got it wrong"):** the interval resets to **1 day**,
  regardless of history. A lapse means the card needs to come back soon.
- **Rating 3, 4, or 5, first time you've ever rated this card:** interval is 3, 7, or 14
  days respectively — a reasonable first guess before there's any history to lean on.
- **Rating 3, 4, or 5, and you've rated it before:** the *previous* interval is multiplied
  by 1.3, 1.8, or 2.5. Easier ratings grow the interval faster.

The previous interval is recovered from the last `attempts` row for that question
(`next_review_at - practiced_at`) rather than stored as its own column — one fewer thing to
keep in sync, at the cost of one extra query per rating.

## Why 3 / 7 / 14 and 1.3 / 1.8 / 2.5

These aren't derived from a formula — they're a reasonable starting point copied from how
SM-2 tends to behave in practice for the first few repetitions, chosen for something that
feels right (a "medium" rating roughly doubles the gap, an "easy" rating stretches it much
further) without the bookkeeping of a true per-card ease factor. They're constants in one
file specifically so they're easy to tune later against real usage data.

## A worked example

Starting from a fresh card, rated 4 ("good") every single time:

| Review # | Rating | Previous interval | New interval | Next review |
|---|---|---|---|---|
| 1 | 4 | none | 7 days | day 7 |
| 2 | 4 | 7 | round(7 × 1.8) = 13 | day 20 |
| 3 | 4 | 13 | round(13 × 1.8) = 23 | day 43 |
| 4 | 4 | 23 | round(23 × 1.8) = 41 | day 84 |

Now suppose review #3 had been a lapse (rating 2) instead:

| Review # | Rating | Previous interval | New interval | Next review |
|---|---|---|---|---|
| 1 | 4 | none | 7 | day 7 |
| 2 | 4 | 7 | 13 | day 20 |
| 3 | **2** | 13 | **1** (lapse, ignores history) | day 21 |
| 4 | 4 | 1 | round(1 × 1.8) = 2 | day 23 |

The lapse doesn't just fail to grow the interval — it throws away the accumulated interval
entirely and starts the climb over from 1 day, same as SM-2's own lapse behavior.

## Known limitation

There's currently no maximum interval and no ease factor, so a long streak of "easy" ratings
on one card can push its interval arbitrarily far out (14 → 35 → 88 → 219 → 548 days) with
no ceiling. A real SM-2/FSRS implementation caps this (365 days is a common ceiling) and
adds per-card jitter so cards captured in the same batch don't all come due on the same day
forever. That's a documented gap, not an oversight — see the code review this app was built
against for the full list of what's next.
