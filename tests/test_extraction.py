import json

from app.practice.extraction import parse_llm_json_fields, parse_markers

RAW_TEXT = """Q: What is a hash map?
A: A data structure that maps keys to values using a hash function.
Example: Python's dict is a hash map.
Topic: data structures
Company: Google
Difficulty: medium
"""


def test_parse_markers_extracts_all_fields():
    fields = parse_markers(RAW_TEXT)
    assert fields["question"] == "What is a hash map?"
    assert "hash function" in fields["answer"]
    assert fields["example"] == "Python's dict is a hash map."
    assert fields["topic"] == "data structures"
    assert fields["company"] == "Google"
    assert fields["difficulty"] == "medium"


def test_parse_markers_defaults_missing_fields_to_empty():
    fields = parse_markers("Q: What is recursion?\nA: A function calling itself.")
    assert fields["question"] == "What is recursion?"
    assert fields["topic"] == ""
    assert fields["company"] == ""
    assert fields["difficulty"] == "medium"


def test_parse_llm_json_fields_strips_markdown_fences():
    payload = {
        "question": "What is Big O?",
        "answer": "A notation for algorithmic complexity.",
        "example": "",
        "topic": "algorithms",
        "company": "",
        "difficulty": "easy",
        "code_snippet": "",
        "language": "",
    }
    wrapped = "```json\n" + json.dumps(payload) + "\n```"
    fields = parse_llm_json_fields(wrapped)
    assert fields["question"] == "What is Big O?"
    assert fields["topic"] == "algorithms"
    assert fields["difficulty"] == "easy"


def test_parse_llm_json_fields_missing_keys_default_empty():
    fields = parse_llm_json_fields(json.dumps({"question": "What is a mutex?"}))
    assert fields["question"] == "What is a mutex?"
    assert fields["answer"] == ""
    assert fields["difficulty"] == "medium"
