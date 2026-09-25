import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_rsvp import register
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.rsvp import RsvpStates, delay_multiplier, words


@pytest.fixture
def service(tmp_path):
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    item_id = store.create_typed_item(
        item_type="article",
        title="Unicode article",
        content='“ Привет ” мир … café, 你好! final longwordhere',
    )
    yield RsvpStates(store), item_id, store
    store.close()


def test_words_preserve_unicode_offsets_and_attach_standalone_punctuation():
    text = '“ Привет ” мир … café, 你好! final'
    result = words(text)
    assert [entry["text"] for entry in result] == ['“Привет”', 'мир…', 'café,', '你好!', 'final']
    assert text[result[0]["start"]:result[0]["end"]].replace(" ", "") == '“Привет”'
    assert text[result[1]["start"]:result[1]["end"]].replace(" ", "") == 'мир…'
    assert result[-1]["end"] == len(text)


@pytest.mark.parametrize(("text", "expected"), [
    ("word", 1.0),
    ("encyclopedia", 1.15),
    ("clause,", 1.3),
    ("clause:”", 1.3),
    ("finished!", 1.8),
    ("finished.)", 1.8),
    ("世界。", 1.8),
])
def test_delay_multiplier_reports_punctuation_and_long_word_timing(text, expected):
    assert delay_multiplier(text) == expected


def test_open_defaults_are_bound_to_actual_item(service):
    rsvp, item_id, _ = service
    state = rsvp.get(item_id)
    assert state == {
        "item_id": item_id,
        "title": "Unicode article",
        "word_index": 0,
        "wpm": 350,
        "chunk_size": 1,
        "bookmark_index": None,
        "word_count": 6,
        "content_revision": state["content_revision"],
        "content_changed": False,
        "updated_at": None,
    }
    assert len(state["content_revision"]) == 64


def test_state_survives_service_reconstruction(service):
    rsvp, item_id, store = service
    revision = rsvp.get(item_id)["content_revision"]
    saved = rsvp.save(item_id, {
        "word_index": 3, "wpm": 525, "chunk_size": 2, "content_revision": revision,
    })
    assert saved["word_index"] == 3
    assert saved["wpm"] == 525
    assert saved["chunk_size"] == 2
    reopened = RsvpStates(store).get(item_id)
    assert reopened["word_index"] == 3
    assert reopened["wpm"] == 525
    assert reopened["chunk_size"] == 2
    assert reopened["updated_at"]


def test_chunk_size_change_never_reinterprets_canonical_offset(service):
    rsvp, item_id, _ = service
    revision = rsvp.get(item_id)["content_revision"]
    one = rsvp.save(item_id, {
        "word_index": 4, "wpm": 350, "chunk_size": 1, "content_revision": revision,
    })
    two = rsvp.save(item_id, {
        "word_index": one["word_index"], "wpm": 350, "chunk_size": 2, "content_revision": revision,
    })
    assert one["word_index"] == two["word_index"] == 4


def test_bookmark_and_restore_reopen_exact_word(service):
    rsvp, item_id, _ = service
    revision = rsvp.get(item_id)["content_revision"]
    bookmarked = rsvp.bookmark(item_id, {"word_index": 2, "content_revision": revision})
    assert bookmarked["bookmark_index"] == 2
    rsvp.save(item_id, {
        "word_index": 5, "wpm": 600, "chunk_size": 1, "content_revision": revision,
    })
    restored = rsvp.restore(item_id)
    assert restored["word_index"] == 2
    assert restored["bookmark_index"] == 2
    assert restored["wpm"] == 600


def test_restore_without_bookmark_is_explicit(service):
    rsvp, item_id, _ = service
    with pytest.raises(CaptureError, match="No RSVP bookmark") as caught:
        rsvp.restore(item_id)
    assert caught.value.status == 409


@pytest.mark.parametrize("payload,message", [
    ({}, "invalid request shape"),
    ({"word_index": 0, "wpm": 350, "chunk_size": 1, "content_revision": "x", "home": "/tmp"}, "invalid request shape"),
    ({"word_index": -1, "wpm": 350, "chunk_size": 1, "content_revision": "REV"}, "word_index"),
    ({"word_index": 99, "wpm": 350, "chunk_size": 1, "content_revision": "REV"}, "word_index"),
    ({"word_index": True, "wpm": 350, "chunk_size": 1, "content_revision": "REV"}, "word_index"),
    ({"word_index": 0, "wpm": 99, "chunk_size": 1, "content_revision": "REV"}, "wpm"),
    ({"word_index": 0, "wpm": 1001, "chunk_size": 1, "content_revision": "REV"}, "wpm"),
    ({"word_index": 0, "wpm": True, "chunk_size": 1, "content_revision": "REV"}, "wpm"),
    ({"word_index": 0, "wpm": 350, "chunk_size": 3, "content_revision": "REV"}, "chunk_size"),
])
def test_save_rejects_invalid_offsets_settings_and_selectors(service, payload, message):
    rsvp, item_id, _ = service
    if payload.get("content_revision") == "REV":
        payload["content_revision"] = rsvp.get(item_id)["content_revision"]
    with pytest.raises(CaptureError, match=message):
        rsvp.save(item_id, payload)


def test_stale_write_rejected_and_reopen_reconciles_changed_content(service):
    rsvp, item_id, store = service
    original = rsvp.get(item_id)
    rsvp.save(item_id, {
        "word_index": 5, "wpm": 450, "chunk_size": 2,
        "content_revision": original["content_revision"],
    })
    store.update_item(item_id, content="Replaced short body")
    with pytest.raises(CaptureError, match="content changed") as caught:
        rsvp.save(item_id, {
            "word_index": 0, "wpm": 450, "chunk_size": 2,
            "content_revision": original["content_revision"],
        })
    assert caught.value.status == 409
    reconciled = rsvp.get(item_id)
    assert reconciled["content_changed"] is True
    assert reconciled["word_index"] == 2
    persisted = rsvp.get(item_id)
    assert persisted["content_changed"] is False
    assert persisted["word_index"] == 2


def test_missing_archived_and_empty_items_are_unavailable(service):
    rsvp, item_id, store = service
    with pytest.raises(CaptureError) as missing:
        rsvp.get("missing")
    assert missing.value.status == 404
    store.update_item(item_id, is_archived=1)
    with pytest.raises(CaptureError) as archived:
        rsvp.get(item_id)
    assert archived.value.status == 404
    empty = store.create_typed_item(item_type="note", title="Empty", content="  \n ")
    with pytest.raises(CaptureError) as no_words:
        rsvp.get(empty)
    assert no_words.value.status == 409


@pytest.mark.asyncio
async def test_http_round_trip_guards_shape_and_persists_bookmark(tmp_path):
    store = KnowledgeStore(str(tmp_path / "http.db"))
    item_id = store.create_typed_item(item_type="article", title="HTTP RSVP", content="one two three four five")
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = store
    app = web.Application()
    app["state"] = state
    register(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        opened_response = await client.get(f"/api/capabilities/knowledge/rsvp/{item_id}")
        assert opened_response.status == 200
        opened = await opened_response.json()
        saved_response = await client.put(f"/api/capabilities/knowledge/rsvp/{item_id}", json={
            "word_index": 2, "wpm": 500, "chunk_size": 2,
            "content_revision": opened["content_revision"],
        })
        assert saved_response.status == 200
        assert (await saved_response.json())["word_index"] == 2
        marked_response = await client.post(f"/api/capabilities/knowledge/rsvp/{item_id}/bookmark", json={
            "word_index": 2, "content_revision": opened["content_revision"],
        })
        assert marked_response.status == 200
        moved_response = await client.put(f"/api/capabilities/knowledge/rsvp/{item_id}", json={
            "word_index": 4, "wpm": 500, "chunk_size": 1,
            "content_revision": opened["content_revision"],
        })
        assert moved_response.status == 200
        restored_response = await client.post(f"/api/capabilities/knowledge/rsvp/{item_id}/restore", json={})
        assert restored_response.status == 200
        assert (await restored_response.json())["word_index"] == 2
        assert (await client.get(f"/api/capabilities/knowledge/rsvp/{item_id}?home=/tmp")).status == 400
        invalid_response = await client.put(f"/api/capabilities/knowledge/rsvp/{item_id}", json={
            "word_index": 2, "wpm": 500, "chunk_size": 2,
            "content_revision": opened["content_revision"], "path": "/tmp",
        })
        assert invalid_response.status == 400
        missing_response = await client.get("/api/capabilities/knowledge/rsvp/missing")
        assert missing_response.status == 404
    finally:
        await client.close()
        store.close()
