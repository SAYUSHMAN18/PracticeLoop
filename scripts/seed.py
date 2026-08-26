"""Load data/starter_deck.json for an existing user.

Usage:
    python scripts/seed.py user@example.com
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.auth.service import get_user_by_email  # noqa: E402
from app.core.db import close_pool, get_pool  # noqa: E402
from app.practice.service import seed_starter_deck  # noqa: E402


async def main(email: str) -> None:
    pool = await get_pool()
    user = await get_user_by_email(pool, email)
    if user is None:
        print(f"No user with email {email!r}.")
        return

    count = await seed_starter_deck(pool, user["user_id"])
    print(f"Seeded {count} questions for {email}.")
    await close_pool()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
