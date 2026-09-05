from __future__ import annotations

import pytest

from app.core.db import get_pool
from app.decks import service
from app.practice.service import create_question, list_questions
from tests.conftest import signup


async def _add_question(pool, user_id: int, question: str, topic: str, answer: str = "") -> int:
    return await create_question(pool, user_id, {"question": question, "answer": answer, "topic": topic})


async def test_publishing_a_deck_snapshots_the_topics_questions(client):
    await signup(client, "deck-publisher@example.com")
    pool = await get_pool()
    from app.auth.service import get_user_by_email

    user = await get_user_by_email(pool, "deck-publisher@example.com")
    await _add_question(pool, user["user_id"], "What is a hash map?", "DSA", "A key-value store.")
    await _add_question(pool, user["user_id"], "What is a binary tree?", "DSA", "A hierarchical structure.")
    await _add_question(pool, user["user_id"], "Unrelated question", "Other topic")

    deck_id = await service.publish_deck(
        pool, user["user_id"], name="DSA Basics", description="Core data structures", topic="DSA"
    )
    deck = await service.get_deck_detail(pool, deck_id)
    assert deck["name"] == "DSA Basics"
    assert deck["question_count"] == 2
    assert len(deck["questions"]) == 2
    assert {q["question"] for q in deck["questions"]} == {"What is a hash map?", "What is a binary tree?"}


async def test_publishing_an_empty_topic_is_rejected():
    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "deck-empty-topic@example.com", "testpassword123", "Test")
    with pytest.raises(service.EmptyTopic):
        await service.publish_deck(pool, user_id, name="Empty", description="", topic="Nonexistent")


async def test_publishing_requires_a_name():
    pool = await get_pool()
    from app.auth.service import create_user

    user_id = await create_user(pool, "deck-noname@example.com", "testpassword123", "Test")
    await _add_question(pool, user_id, "Q1", "Topic")
    with pytest.raises(service.NameRejected):
        await service.publish_deck(pool, user_id, name="   ", description="", topic="Topic")


async def test_importing_a_deck_copies_into_the_importers_bank():
    pool = await get_pool()
    from app.auth.service import create_user

    owner_id = await create_user(pool, "deck-import-owner@example.com", "testpassword123", "Owner")
    await _add_question(pool, owner_id, "What is Big-O?", "CS", "Asymptotic complexity.")
    deck_id = await service.publish_deck(pool, owner_id, name="CS 101", description="", topic="CS")

    importer_id = await create_user(pool, "deck-import-student@example.com", "testpassword123", "Student")
    result = await service.import_deck(pool, importer_id, deck_id)
    assert result == {"added": 1, "skipped": 0}

    imported = await list_questions(pool, importer_id)
    assert len(imported) == 1
    assert imported[0]["question"] == "What is Big-O?"
    assert imported[0]["source"] == "shared_deck"

    deck = await service.get_deck_detail(pool, deck_id)
    assert deck["import_count"] == 1


async def test_importing_dedupes_against_the_importers_own_bank():
    pool = await get_pool()
    from app.auth.service import create_user

    owner_id = await create_user(pool, "deck-dedupe-owner@example.com", "testpassword123", "Owner")
    await _add_question(pool, owner_id, "Duplicate question", "CS", "Answer A")
    deck_id = await service.publish_deck(pool, owner_id, name="Dupe deck", description="", topic="CS")

    importer_id = await create_user(pool, "deck-dedupe-student@example.com", "testpassword123", "Student")
    await _add_question(pool, importer_id, "Duplicate question", "CS", "My own answer")

    result = await service.import_deck(pool, importer_id, deck_id)
    assert result == {"added": 0, "skipped": 1}
    assert len(await list_questions(pool, importer_id)) == 1


async def test_editing_the_owners_question_after_publishing_does_not_change_the_deck():
    """The snapshot property this whole design rests on: shared_deck_questions
    holds its own copy, so the owner's later edits (or deletes) never
    reach a deck someone might already be reading or importing from."""
    pool = await get_pool()
    from app.auth.service import create_user

    owner_id = await create_user(pool, "deck-snapshot-owner@example.com", "testpassword123", "Owner")
    question_id = await _add_question(pool, owner_id, "Original wording", "CS", "Original answer")
    deck_id = await service.publish_deck(pool, owner_id, name="Snapshot deck", description="", topic="CS")

    await pool.execute("UPDATE questions SET question = 'Edited wording' WHERE question_id = $1", question_id)
    await pool.execute("DELETE FROM questions WHERE question_id = $1", question_id)

    deck = await service.get_deck_detail(pool, deck_id)
    assert deck["questions"][0]["question"] == "Original wording"


async def test_a_non_owner_cannot_delete_a_deck():
    pool = await get_pool()
    from app.auth.service import create_user

    owner_id = await create_user(pool, "deck-delete-owner@example.com", "testpassword123", "Owner")
    await _add_question(pool, owner_id, "Q", "CS")
    deck_id = await service.publish_deck(pool, owner_id, name="Protected deck", description="", topic="CS")

    attacker_id = await create_user(pool, "deck-delete-attacker@example.com", "testpassword123", "Attacker")
    with pytest.raises(service.DeckNotFound):
        await service.delete_deck(pool, attacker_id, deck_id)


async def test_deleting_a_deck_leaves_already_imported_copies_intact():
    pool = await get_pool()
    from app.auth.service import create_user

    owner_id = await create_user(pool, "deck-unpublish-owner@example.com", "testpassword123", "Owner")
    await _add_question(pool, owner_id, "Q", "CS", "A")
    deck_id = await service.publish_deck(pool, owner_id, name="Temp deck", description="", topic="CS")

    importer_id = await create_user(pool, "deck-unpublish-student@example.com", "testpassword123", "Student")
    await service.import_deck(pool, importer_id, deck_id)

    await service.delete_deck(pool, owner_id, deck_id)

    with pytest.raises(service.DeckNotFound):
        await service.get_deck_detail(pool, deck_id)
    assert len(await list_questions(pool, importer_id)) == 1


async def test_gallery_search_filters_by_name_or_topic():
    pool = await get_pool()
    from app.auth.service import create_user

    owner_id = await create_user(pool, "deck-search-owner@example.com", "testpassword123", "Owner")
    await _add_question(pool, owner_id, "Q1", "Biology")
    await _add_question(pool, owner_id, "Q2", "Chemistry")
    await service.publish_deck(pool, owner_id, name="Cell Biology", description="", topic="Biology")
    await service.publish_deck(pool, owner_id, name="Organic Chem", description="", topic="Chemistry")

    results = await service.list_public_decks(pool, query="biology")
    assert [d["name"] for d in results] == ["Cell Biology"]


async def test_decks_index_page_renders(client):
    await signup(client, "deck-index-page@example.com")
    response = await client.get("/decks")
    assert response.status_code == 200
    assert "Shared Decks" in response.text


async def test_publish_route_and_import_route_end_to_end(client):
    await signup(client, "deck-route-owner@example.com")
    pool = await get_pool()
    from app.auth.service import get_user_by_email

    owner = await get_user_by_email(pool, "deck-route-owner@example.com")
    await _add_question(pool, owner["user_id"], "Route question", "Networking", "Route answer")

    publish = await client.post(
        "/decks",
        data={"name": "Networking Basics", "description": "", "topic": "Networking"},
        follow_redirects=False,
    )
    assert publish.status_code == 303
    deck_id = publish.headers["location"].rsplit("/", 1)[-1]

    from httpx import ASGITransport, AsyncClient

    from app.main import app

    importer = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await signup(importer, "deck-route-student@example.com")
    imported = await importer.post(f"/decks/{deck_id}/import")
    assert imported.status_code == 200
    assert "Added 1 new question" in imported.text
    await importer.aclose()


async def test_deck_detail_404s_for_a_nonexistent_deck(client):
    await signup(client, "deck-404@example.com")
    response = await client.get("/decks/999999999")
    assert response.status_code == 404
