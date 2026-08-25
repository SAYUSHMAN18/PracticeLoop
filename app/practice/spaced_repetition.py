from __future__ import annotations

from datetime import date, timedelta

# 1 = blackout, 5 = trivially easy -- same rating scale as PrepGuru's qa_attempts.
_FIRST_INTERVAL_DAYS = {3: 3, 4: 7, 5: 14}
_GROWTH_MULTIPLIER = {3: 1.3, 4: 1.8, 5: 2.5}
_LAPSE_INTERVAL_DAYS = 1


def next_interval_days(rating: int, previous_interval_days: int | None) -> int:
    """Simplified SM-2-style scheduling: a lapse (rating <= 2) resets to a
    1-day interval; otherwise the interval grows from whatever it was last
    time, scaled by how easy this attempt felt."""
    if rating not in range(1, 6):
        raise ValueError(f"rating must be 1-5, got {rating}")

    if rating <= 2:
        return _LAPSE_INTERVAL_DAYS

    if previous_interval_days is None:
        return _FIRST_INTERVAL_DAYS[rating]

    return max(1, round(previous_interval_days * _GROWTH_MULTIPLIER[rating]))


def next_review_date(
    rating: int, previous_interval_days: int | None, today: date | None = None
) -> tuple[date, int]:
    today = today or date.today()
    interval = next_interval_days(rating, previous_interval_days)
    return today + timedelta(days=interval), interval
