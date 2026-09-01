from datetime import date, timedelta

from app.auth.service import create_user
from app.core.db import get_pool
from app.dashboard.service import topic_mastery
from app.practice.service import create_question, record_attempt


async def _make_question(pool, email: str, topic: str, difficulty: str = "medium") -> tuple[int, int]:
    user_id = await create_user(pool, email, "testpassword123", "Test")
    question_id = await create_question(
        pool, user_id, {"question": "Q", "answer": "A", "topic": topic, "difficulty": difficulty}
    )
    return user_id, question_id


async def _backdate(pool, question_id: int, days_ago: int) -> None:
    """record_attempt always stamps practiced_at = now() -- tests that
    care about recency weighting push it into the past afterward rather
    than needing a practiced_at param the app itself never uses."""
    await pool.execute(
        "UPDATE attempts SET practiced_at = $2 WHERE question_id = $1",
        question_id,
        date.today() - timedelta(days=days_ago),
    )


async def test_topic_mastery_is_empty_with_no_attempts():
    pool = await get_pool()
    user_id = await create_user(pool, "mastery-empty@example.com", "testpassword123", "Test")
    assert await topic_mastery(pool, user_id) == []


async def test_recent_attempts_outweigh_old_ones():
    pool = await get_pool()
    # One user, two topics: "fresh" was rated low long ago but high
    # recently; "stale" is the mirror image. A plain average would tie
    # them at 3.0 -- recency weighting should pull "fresh" ahead.
    user_id, q_fresh = await _make_question(pool, "mastery-recency@example.com", "fresh")
    await record_attempt(pool, user_id, q_fresh, rating=1)
    await _backdate(pool, q_fresh, days_ago=60)
    q_fresh2 = await create_question(
        pool, user_id, {"question": "Q2", "answer": "A", "topic": "fresh", "difficulty": "medium"}
    )
    await record_attempt(pool, user_id, q_fresh2, rating=5)

    q_stale = await create_question(
        pool, user_id, {"question": "Q3", "answer": "A", "topic": "stale", "difficulty": "medium"}
    )
    await record_attempt(pool, user_id, q_stale, rating=5)
    await _backdate(pool, q_stale, days_ago=60)
    q_stale2 = await create_question(
        pool, user_id, {"question": "Q4", "answer": "A", "topic": "stale", "difficulty": "medium"}
    )
    await record_attempt(pool, user_id, q_stale2, rating=1)

    results = {r["topic"]: r for r in await topic_mastery(pool, user_id)}
    assert results["fresh"]["avg_confidence"] == results["stale"]["avg_confidence"] == 3.0
    assert results["fresh"]["mastery_score"] > results["stale"]["mastery_score"]


async def test_harder_topic_scores_higher_at_equal_confidence():
    pool = await get_pool()
    user_id, q_easy = await _make_question(pool, "mastery-difficulty@example.com", "easy-topic", "easy")
    await record_attempt(pool, user_id, q_easy, rating=4)

    q_hard = await create_question(
        pool, user_id, {"question": "Q2", "answer": "A", "topic": "hard-topic", "difficulty": "hard"}
    )
    await record_attempt(pool, user_id, q_hard, rating=4)

    results = {r["topic"]: r for r in await topic_mastery(pool, user_id)}
    assert results["easy-topic"]["avg_confidence"] == results["hard-topic"]["avg_confidence"] == 4.0
    assert results["hard-topic"]["mastery_score"] > results["easy-topic"]["mastery_score"]


async def test_single_attempt_shrinks_toward_neutral_prior():
    pool = await get_pool()
    user_id, question_id = await _make_question(pool, "mastery-shrink@example.com", "one-shot")
    await record_attempt(pool, user_id, question_id, rating=5)

    results = await topic_mastery(pool, user_id)
    assert len(results) == 1
    # Unshrunk, a single 5/5 (with no retrievability yet -- schedule_review
    # runs inside record_attempt, but get_card_retrievability right after
    # a first review is near 1.0 too) would land near 100; shrinkage toward
    # the 50 prior on one attempt should pull it well below that.
    assert results[0]["mastery_score"] < 90


async def test_more_attempts_shrink_less_than_one_attempt():
    pool = await get_pool()
    user_id, question_id = await _make_question(pool, "mastery-shrink-more@example.com", "many-shots")
    for _ in range(6):
        q = await create_question(
            pool, user_id, {"question": "Q", "answer": "A", "topic": "many-shots", "difficulty": "medium"}
        )
        await record_attempt(pool, user_id, q, rating=5)

    q_single = await create_question(
        pool, user_id, {"question": "Q", "answer": "A", "topic": "one-shot-2", "difficulty": "medium"}
    )
    await record_attempt(pool, user_id, q_single, rating=5)

    results = {r["topic"]: r for r in await topic_mastery(pool, user_id)}
    assert results["many-shots"]["mastery_score"] > results["one-shot-2"]["mastery_score"]


async def test_weakest_topic_sorts_first():
    pool = await get_pool()
    user_id, q_weak = await _make_question(pool, "mastery-sort@example.com", "weak")
    for _ in range(4):
        q = await create_question(
            pool, user_id, {"question": "Q", "answer": "A", "topic": "weak", "difficulty": "medium"}
        )
        await record_attempt(pool, user_id, q, rating=1)
    await record_attempt(pool, user_id, q_weak, rating=1)

    q_strong = await create_question(
        pool, user_id, {"question": "Q", "answer": "A", "topic": "strong", "difficulty": "medium"}
    )
    for _ in range(4):
        q = await create_question(
            pool, user_id, {"question": "Q", "answer": "A", "topic": "strong", "difficulty": "medium"}
        )
        await record_attempt(pool, user_id, q, rating=5)
    await record_attempt(pool, user_id, q_strong, rating=5)

    results = await topic_mastery(pool, user_id)
    topics_in_order = [r["topic"] for r in results]
    assert topics_in_order.index("weak") < topics_in_order.index("strong")


async def test_mastery_score_always_in_bounds():
    pool = await get_pool()
    user_id, question_id = await _make_question(pool, "mastery-bounds@example.com", "t", "hard")
    await record_attempt(pool, user_id, question_id, rating=5)
    for r in await topic_mastery(pool, user_id):
        assert 0 <= r["mastery_score"] <= 100
