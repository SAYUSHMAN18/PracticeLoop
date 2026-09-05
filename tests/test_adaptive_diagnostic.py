"""The diagnostic's adaptive engine (app/assessments/service.py): a plain
staircase over a pre-generated item bank, no LLM involved -- right answer
moves the difficulty tier up, wrong answer moves it down, and the next
question administered is whichever unused pool item is closest to the
current tier. Covered here as pure state-machine logic, independent of the
HTTP/session plumbing that drives it in app/assessments/router.py.
"""

from app.assessments.service import (
    DIFFICULTIES,
    QUESTION_COUNT,
    answer_current_question,
    current_question,
    is_complete,
    next_tier,
    pick_next_question,
    start_session,
    summarize,
)


def _item(difficulty: str, correct_choice_index: int = 0, subtopic: str = "") -> dict:
    return {
        "question": f"a {difficulty} question",
        "subtopic": subtopic,
        "difficulty": difficulty,
        "choices": ["right", "wrong"],
        "correct_choice_index": correct_choice_index,
    }


def test_next_tier_moves_up_on_correct_and_down_on_incorrect():
    assert next_tier(1, was_correct=True) == 2
    assert next_tier(1, was_correct=False) == 0


def test_next_tier_clamps_at_the_ends():
    assert next_tier(len(DIFFICULTIES) - 1, was_correct=True) == len(DIFFICULTIES) - 1
    assert next_tier(0, was_correct=False) == 0


def test_pick_next_question_prefers_an_exact_tier_match():
    pool = [_item("easy"), _item("medium"), _item("hard")]
    assert pool[pick_next_question(pool, [], tier=2)]["difficulty"] == "hard"
    assert pool[pick_next_question(pool, [], tier=0)]["difficulty"] == "easy"


def test_pick_next_question_widens_when_its_tier_is_exhausted():
    """Every "hard" question already asked -- a student acing everything
    shouldn't have the diagnostic quit early just because that tier ran
    out first. It should fall back to the next-closest tier (medium)."""
    pool = [_item("easy"), _item("medium"), _item("hard"), _item("hard")]
    asked = [2, 3]  # both hard questions used
    next_index = pick_next_question(pool, asked, tier=2)
    assert pool[next_index]["difficulty"] == "medium"


def test_pick_next_question_returns_none_once_the_pool_is_exhausted():
    pool = [_item("easy")]
    assert pick_next_question(pool, [0], tier=1) is None


def test_current_question_never_leaks_the_answer_key():
    pool = [_item("medium", correct_choice_index=1)]
    state = start_session(pool)
    question = current_question(state)
    assert set(question) == {"question", "choices"}


def test_a_full_run_administers_exactly_question_count_questions():
    pool = [_item("medium") for _ in range(len(DIFFICULTIES) * 5)]  # a full-size bank, one tier
    state = start_session(pool)

    asked = 0
    while not is_complete(state):
        answer_current_question(state, selected_index=0)  # always "right"
        asked += 1

    assert asked == QUESTION_COUNT
    correct_count, total_count, weak_subtopics = summarize(state)
    assert (correct_count, total_count, weak_subtopics) == (QUESTION_COUNT, QUESTION_COUNT, [])


def test_a_run_ends_early_when_the_pool_is_smaller_than_question_count():
    pool = [_item("medium"), _item("medium"), _item("medium")]
    assert len(pool) < QUESTION_COUNT
    state = start_session(pool)

    asked = 0
    while not is_complete(state):
        answer_current_question(state, selected_index=0)
        asked += 1

    assert asked == len(pool)


def test_wrong_answers_are_tallied_and_their_subtopics_collected():
    pool = [
        _item("medium", correct_choice_index=0, subtopic="topic-a"),
        _item("medium", correct_choice_index=0, subtopic="topic-b"),
    ]
    state = start_session(pool)

    answer_current_question(state, selected_index=0)  # right, topic-a
    answer_current_question(state, selected_index=1)  # wrong, topic-b

    assert is_complete(state)
    correct_count, total_count, weak_subtopics = summarize(state)
    assert (correct_count, total_count) == (1, 2)
    assert weak_subtopics == ["topic-b"]
