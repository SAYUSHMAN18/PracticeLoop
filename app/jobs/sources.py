from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RawListing:
    """A pluggable source's own listing, before it's scored or stored.
    Adding a second source (Greenhouse/Lever public board APIs) means
    writing one more fetch_* function with this same signature -- nothing
    downstream needs to know which source a listing came from beyond the
    `source` string it stamps on."""

    source: str
    external_id: str
    title: str
    company: str
    location: str
    description: str
    url: str


async def fetch_adzuna(keywords: str, location: str = "", max_results: int = 25) -> list[RawListing]:
    """Adzuna's official job search API: free key, no scraping, covers
    India and most major markets, ample quota for a daily digest.

    Returns [] -- not an error -- when no credentials are configured, so a
    discovery run with zero sources set up is a no-op rather than a crash;
    the caller decides whether a run with nothing to search is worth
    recording as such.
    """
    app_id = settings.adzuna_app_id.strip()
    app_key = settings.adzuna_app_key.strip()
    if not app_id or not app_key:
        return []

    country = settings.adzuna_country.strip().lower() or "in"
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": keywords,
        "results_per_page": max(1, min(max_results, 50)),
        "content-type": "application/json",
    }
    if location:
        params["where"] = location

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()

    listings = []
    for item in payload.get("results", []):
        external_id = str(item.get("id") or "").strip()
        title = (item.get("title") or "").strip()
        if not external_id or not title:
            continue  # can't dedupe or show a listing with no id/title
        listings.append(
            RawListing(
                source="adzuna",
                external_id=external_id,
                title=title,
                company=((item.get("company") or {}).get("display_name") or "").strip(),
                location=((item.get("location") or {}).get("display_name") or "").strip(),
                description=(item.get("description") or "").strip(),
                url=(item.get("redirect_url") or "").strip(),
            )
        )
    return listings


# Every configured source, tried independently per user -- one source
# failing (a timeout, a quota error) doesn't stop the others from running;
# see jobs/service.py's per-source try/except.
SOURCES = (fetch_adzuna,)
