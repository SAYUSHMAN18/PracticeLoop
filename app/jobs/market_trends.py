from __future__ import annotations

from collections import Counter

import asyncpg

# A curated list rather than an LLM call per listing: running extraction on
# every discovered listing (potentially many, across many users) on every
# view would be slow and, with a real LLM key configured, not free. This
# also means the trends page works with zero LLM configuration, same as
# discovery's keyword fit scoring.
_KNOWN_SKILLS = [
    "Python", "JavaScript", "TypeScript", "Java", "Go", "Rust", "C++", "C#",
    "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Docker", "Kubernetes",
    "AWS", "GCP", "Azure", "Terraform", "React", "Vue", "Angular", "Node.js",
    "FastAPI", "Django", "Flask", "Spring", "GraphQL", "REST", "Kafka",
    "RabbitMQ", "Elasticsearch", "CI/CD", "Git", "Linux", "Machine Learning",
    "TensorFlow", "PyTorch", "NLP", "Spark", "Airflow", "Microservices",
    "System Design", "Agile", "Scrum",
]  # fmt: skip

# Below this many listings, a "top skill" ranking is more noise than signal
# -- one or two listings mentioning a skill can dominate a tiny sample. The
# plan is explicit about this: "a trend computed from 40 listings is a
# hint, not a finding, and should say so."
_SMALL_SAMPLE_THRESHOLD = 40


def tag_skills(text: str) -> list[str]:
    """Which of _KNOWN_SKILLS appear in a piece of text -- the same
    keyword match compute_skill_demand runs per-listing, exposed so a
    single posting (e.g. a classroom opportunity) can show its own
    skill tags, not just the system-wide aggregate."""
    lowered = text.lower()
    return [skill for skill in _KNOWN_SKILLS if skill.lower() in lowered]


async def compute_skill_demand(pool: asyncpg.Pool, limit: int = 15) -> dict:
    """Aggregates across every discovered listing in the system, not just
    one user's own -- deliberately not scoped by user_id, unlike everything
    else in this app. This is the one feature where that's the right call:
    market demand is public information about what job postings ask for,
    not a user's private data, and a student with zero discovered listings
    of their own still needs an entry point into what the market wants.
    """
    listings = await pool.fetch("SELECT title, description FROM job_listings")
    total_listings = len(listings)

    counts: Counter[str] = Counter()
    for listing in listings:
        counts.update(tag_skills(f"{listing['title']} {listing['description']}"))

    return {
        "total_listings": total_listings,
        "top_skills": counts.most_common(limit),
        "small_sample": total_listings < _SMALL_SAMPLE_THRESHOLD,
    }
