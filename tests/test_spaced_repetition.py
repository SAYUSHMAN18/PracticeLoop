from datetime import date

import pytest

from app.practice.spaced_repetition import next_interval_days, next_review_date


def test_lapse_always_resets_to_one_day():
    assert next_interval_days(1, previous_interval_days=30) == 1
    assert next_interval_days(2, previous_interval_days=30) == 1


def test_first_success_uses_fixed_interval():
    assert next_interval_days(3, previous_interval_days=None) == 3
    assert next_interval_days(4, previous_interval_days=None) == 7
    assert next_interval_days(5, previous_interval_days=None) == 14


def test_interval_grows_from_previous():
    assert next_interval_days(5, previous_interval_days=14) == 35  # round(14 * 2.5)


def test_interval_never_shrinks_below_one_day():
    assert next_interval_days(3, previous_interval_days=0) == 1


@pytest.mark.parametrize("bad_rating", [0, 6, -1])
def test_invalid_rating_rejected(bad_rating):
    with pytest.raises(ValueError):
        next_interval_days(bad_rating, previous_interval_days=None)


def test_next_review_date_returns_date_and_interval():
    review_date, interval = next_review_date(4, previous_interval_days=None, today=date(2026, 1, 1))
    assert interval == 7
    assert review_date == date(2026, 1, 8)
