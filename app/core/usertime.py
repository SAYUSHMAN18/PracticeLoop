"""What day is it *for this user*.

Every spaced-repetition due date, "due today" count, daily-plan rollover
and streak calculation used to run off `date.today()` -- the server's
date, which is UTC on Render. For a learner in India (UTC+5:30) that
means the day flips at 5:30 AM local: a late-evening session lands on the
wrong calendar day, a streak breaks a day early, and cards come due at
the wrong local hour. The NEET and IELTS goal templates point straight at
the audience this hurts most.

`profiles.timezone` is free text from a dropdown, never validated against
the IANA database, so every lookup here degrades to UTC rather than
raising -- a wrong day boundary is bad, a 500 on the dashboard is worse.

`day_rollover_hour` is Anki's "next day starts at" idea: a user who
studies past midnight can set it to 3 or 4 so a 1 AM session still counts
for the day before. 0 (the default) means a plain midnight boundary.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


def resolve_zone(tz_name: str | None) -> tzinfo:
    """An unset, blank, or unrecognized timezone name resolves to UTC."""
    if not tz_name:
        return UTC
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def canonical_zone_name(tz_name: str | None) -> str:
    """A guaranteed-valid IANA name for SQL `AT TIME ZONE`, or "UTC".
    Postgres raises on an unknown zone string, and profiles.timezone is
    unvalidated free text -- so anything that doesn't resolve becomes UTC."""
    if not tz_name:
        return "UTC"
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"
    return tz_name


def now_for(tz_name: str | None) -> datetime:
    """Timezone-aware current instant in the user's zone."""
    return datetime.now(resolve_zone(tz_name))


def today_for(tz_name: str | None, rollover_hour: int = 0) -> date:
    """The user's current study day. With rollover_hour > 0, the first few
    hours after midnight still count as the previous day."""
    local = now_for(tz_name)
    if rollover_hour:
        local -= timedelta(hours=rollover_hour)
    return local.date()


def clamp_rollover_hour(value: object) -> int:
    """Coerce a form value to 0-23; anything invalid falls back to 0."""
    try:
        hour = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return hour if 0 <= hour <= 23 else 0
