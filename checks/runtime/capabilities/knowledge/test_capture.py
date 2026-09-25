"""Capture lifecycle through real knowledge storage, audio bytes and HTTP."""

import asyncio
import hashlib
import io
import json
import wave

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.extensions.providers.use_cases import save_use_case_settings
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_capture import register
from gideon.workspace.capabilities.knowledge.capture import CaptureError, CaptureInbox


@pytest.fixture
def inbox(tmp_path):
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    result = CaptureInbox(store)
    yield result
    store.close()


def route_payload(capture, key="route-request-001", **updates):
    return {"request_id": key, "revision": capture["revision"], "destination": "note", "title": "Reviewed title", "content": "Reviewed content", **updates}


def audio_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(8000)
        recording.writeframes(bytes(1600))
    return buffer.getvalue()


def test_capture_preserves_exact_original(inbox):
    text = "  Original café 🌱\nSecond line with punctuation!  "
    captured = inbox.create("capture-original", text)
    assert captured["text"] == text
    assert captured["input_origin"] == "text"
    assert captured["captured_at"].endswith("+00:00")
    assert captured["status"] == "needs_review"
    assert captured["revision"] == 1
    assert captured["events"] == []
    assert captured["audio_item_id"] is None
    assert captured["destination_id"] is None
    assert inbox.get(captured["id"]) == captured


def test_capture_retry_is_exactly_once(inbox):
    first = inbox.create("capture-retry-1", "A reliable original")
    repeated = inbox.create("capture-retry-1", "A reliable original")
    assert first == repeated
    assert inbox.list()["total"] == 1
    with pytest.raises(CaptureError) as refused:
        inbox.create("capture-retry-1", "Changed original")
    assert refused.value.status == 409
    assert inbox.get(first["id"])["text"] == "A reliable original"


def test_routes_into_each_canonical_knowledge_type(inbox):
    for kind in ("note", "journal", "fleeting"):
        original = inbox.create(f"capture-type-{kind}", f"Original {kind}")
        routed = inbox.route(original["id"], route_payload(original, key=f"route-type-{kind}", destination=kind))
        item = inbox.store.get_item(routed["destination_id"])
        assert item["item_type"] == kind
        assert item["title"] == "Reviewed title"
        assert item["content"] == "Reviewed content"
        assert routed["status"] == "routed"
        assert routed["revision"] == 2
        assert routed["source_link"] == f"#/knowledge/item/{item['id']}"
        assert item["file_metadata"]["capture_id"] == original["id"]
        assert item["file_metadata"]["original_at"] == original["captured_at"]


def test_routing_replay_does_not_create_another_destination(inbox):
    original = inbox.create("capture-routing", "Original routing text")
    payload = route_payload(original)
    first = inbox.route(original["id"], payload)
    replay = inbox.route(original["id"], payload)
    assert first == replay
    assert len(replay["events"]) == 1
    assert inbox.db.execute("SELECT count(*) FROM items").fetchone()[0] == 1
    with pytest.raises(CaptureError) as refused:
        inbox.route(original["id"], payload | {"content": "Different retry"})
    assert refused.value.status == 409


def test_correction_updates_same_destination_and_preserves_history(inbox):
    original = inbox.create("capture-correct", "Immutable raw thought")
    first_payload = route_payload(original)
    first = inbox.route(original["id"], first_payload)
    corrected = inbox.route(original["id"], route_payload(first, key="route-correct-02", destination="fleeting", content="Corrected thought"))
    assert corrected["destination_id"] == first["destination_id"]
    assert corrected["revision"] == 3
    assert corrected["text"] == "Immutable raw thought"
    assert [event["payload"]["content"] for event in corrected["events"]] == ["Reviewed content", "Corrected thought"]
    item = inbox.store.get_item(corrected["destination_id"])
    assert item["item_type"] == "fleeting"
    assert item["content"] == "Corrected thought"
    assert inbox.route(original["id"], first_payload)["revision"] == 3


def test_stale_route_is_refused_without_mutation(inbox):
    original = inbox.create("capture-stale-1", "Original")
    routed = inbox.route(original["id"], route_payload(original))
    with pytest.raises(CaptureError) as refused:
        inbox.route(original["id"], route_payload(original, key="another-stale-route"))
    assert refused.value.status == 409
    assert inbox.get(original["id"]) == routed


def test_removed_destination_does_not_resurrect(inbox):
    original = inbox.create("capture-removed", "Original")
    routed = inbox.route(original["id"], route_payload(original))
    inbox.store.delete_item(routed["destination_id"])
    with pytest.raises(CaptureError) as refused:
        inbox.route(original["id"], route_payload(routed, key="route-after-delete"))
    assert refused.value.status == 409
    assert inbox.get(original["id"])["text"] == "Original"
    assert inbox.db.execute("SELECT count(*) FROM items").fetchone()[0] == 0


def test_durable_pending_route_recovers_existing_destination(inbox):
    original = inbox.create("capture-recover", "Original")
    payload = route_payload(original)
    inbox.db.execute("UPDATE capability_knowledge_captures SET pending=? WHERE id=?", (json.dumps(payload, sort_keys=True), original["id"]))
    inbox.db.commit()
    destination = inbox.store.create_typed_item(item_type="note", title="Reviewed title", content="Reviewed content", guid=f"capture:route:{original['id']}")
    with pytest.raises(CaptureError):
        inbox.route(original["id"], payload | {"request_id": "conflicting-pending"})
    recovered = inbox.route(original["id"], payload)
    assert recovered["destination_id"] == destination
    assert recovered["status"] == "routed"
    assert inbox.db.execute("SELECT count(*) FROM items").fetchone()[0] == 1


def test_original_and_events_reject_in_place_edits(inbox):
    original = inbox.create("capture-tamper-1", "Preserved original")
    routed = inbox.route(original["id"], route_payload(original))
    with pytest.raises(Exception, match="provenance is immutable"):
        inbox.db.execute("UPDATE capability_knowledge_captures SET original_text='changed' WHERE id=?", (original["id"],))
    inbox.db.rollback()
    with pytest.raises(Exception, match="history is immutable"):
        inbox.db.execute("UPDATE capability_knowledge_capture_events SET payload='{}'")
    inbox.db.rollback()
    with pytest.raises(Exception, match="history is immutable"):
        inbox.db.execute("DELETE FROM capability_knowledge_capture_events")
    inbox.db.rollback()
    assert inbox.get(original["id"]) == routed


def test_reopen_retains_original_receipts_and_route(tmp_path):
    path = str(tmp_path / "knowledge.db")
    first_store = KnowledgeStore(path)
    first = CaptureInbox(first_store)
    original = first.create("capture-persist", "Original preserved across restart")
    routed = first.route(original["id"], route_payload(original))
    first_store.close()
    second_store = KnowledgeStore(path)
    second = CaptureInbox(second_store)
    assert second.get(original["id"]) == routed
    assert second.create("capture-persist", "Original preserved across restart")["id"] == original["id"]
    assert second_store.get_item(routed["destination_id"])["content"] == "Reviewed content"
    second_store.close()


def test_chronology_and_bounded_pages(inbox):
    created = [inbox.create(f"capture-page-{index}", f"Thought {index}") for index in range(5)]
    first = inbox.list(limit=2)
    second = inbox.list(limit=2, offset=first["next_offset"])
    third = inbox.list(limit=2, offset=second["next_offset"])
    assert [item["id"] for item in first["items"] + second["items"] + third["items"]] == [item["id"] for item in reversed(created)]
    assert first["total"] == 5
    assert third["next_offset"] is None
    assert inbox.list(offset=9)["items"] == []
    with pytest.raises(CaptureError):
        inbox.list(limit=101)


@pytest.mark.parametrize("key,text", [("bad", "Text"), ("capture-invalid", ""), ("capture-space", "   "), ("capture-long", "x" * 100001), ("capture-number", 42)])
def test_capture_validation_is_persistent_state_safe(inbox, key, text):
    with pytest.raises(CaptureError):
        inbox.create(key, text)
    assert inbox.list()["total"] == 0


@pytest.mark.parametrize("change", [{"revision": True}, {"title": ""}, {"content": " "}, {"destination": "audio"}, {"provider": "external"}, {"request_id": "short"}])
def test_route_validation_cannot_create_destination(inbox, change):
    original = inbox.create("capture-invalid-route", "Original")
    with pytest.raises(CaptureError):
        inbox.route(original["id"], route_payload(original) | change)
    assert inbox.store.db.execute("SELECT count(*) FROM items").fetchone()[0] == 0
    assert inbox.get(original["id"])["revision"] == 1


def test_real_audio_bytes_are_preserved_and_deduplicated(inbox):
    data = audio_bytes()
    voice = inbox.save_audio("capture-audio-01", data, "recording.wav", "audio/wav")
    repeated = inbox.save_audio("capture-audio-01", data, "renamed.wav", "audio/wav")
    assert repeated == voice
    assert voice["input_origin"] == "voice"
    assert voice["audio_sha256"] == hashlib.sha256(data).hexdigest()
    item = inbox.store.get_item(voice["audio_item_id"])
    from pathlib import Path
    assert Path(item["file_path"]).read_bytes() == data
    assert Path(item["file_path"]).parent == inbox.files_root
    assert item["item_type"] == "audio"
    assert item["file_size"] == len(data)
    with pytest.raises(CaptureError) as refused:
        inbox.save_audio("capture-audio-01", data + b"other", "recording.wav", "audio/wav")
    assert refused.value.status == 409


def test_unavailable_real_transcription_preserves_retryable_audio(inbox, monkeypatch, tmp_path):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "no-speech-provider"))
    save_use_case_settings("stt", {"enabled": False})
    inbox = CaptureInbox(inbox.store)
    voice = inbox.save_audio("capture-no-stt-1", audio_bytes(), "recording.wav", "audio/wav")
    result = asyncio.run(inbox.transcribe(voice["id"]))
    assert result["status"] == "transcription_unavailable"
    assert result["transcript"] is None
    assert result["text"] == ""
    assert "preserved" in result["error"]
    assert result["audio_sha256"] == voice["audio_sha256"]
    assert result["revision"] == 2
    again = asyncio.run(inbox.transcribe(voice["id"]))
    assert again["status"] == "transcription_unavailable"
    assert again["revision"] == 3
    assert inbox.store.get_item(voice["audio_item_id"])["file_size"] == len(audio_bytes())


def test_changed_audio_refuses_transcription_before_provider(inbox):
    voice = inbox.save_audio("capture-changed-audio", audio_bytes(), "recording.wav", "audio/wav")
    item = inbox.store.get_item(voice["audio_item_id"])
    from pathlib import Path
    Path(item["file_path"]).write_bytes(b"changed")
    with pytest.raises(CaptureError) as refused:
        asyncio.run(inbox.transcribe(voice["id"]))
    assert refused.value.status == 409
    assert inbox.get(voice["id"])["revision"] == 1


def test_audio_validation_and_wrong_origin(inbox):
    with pytest.raises(CaptureError):
        inbox.save_audio("capture-bad-mime", audio_bytes(), "recording.wav", "text/plain")
    with pytest.raises(CaptureError):
        inbox.save_audio("capture-empty-audio", b"", "recording.wav", "audio/wav")
    with pytest.raises(CaptureError):
        inbox.save_audio("capture-bad-extension", audio_bytes(), "recording.exe", "audio/wav")
    text = inbox.create("capture-text-asr", "Actual text")
    with pytest.raises(CaptureError):
        asyncio.run(inbox.transcribe(text["id"]))
    assert inbox.list()["total"] == 1


def make_app(path):
    store = KnowledgeStore(str(path))
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = store
    app = web.Application()
    app["state"] = state
    register(app)
    return app, store


def test_http_capture_review_retry_and_cross_store_isolation(tmp_path):
    async def journey():
        app, store = make_app(tmp_path / "left.db")
        other, other_store = make_app(tmp_path / "right.db")
        root = "/api/capabilities/knowledge/captures"
        async with TestClient(TestServer(app)) as client, TestClient(TestServer(other)) as isolated:
            response = await client.post(root, json={"request_id": "http-text-capture", "text": "An actual input"})
            original = await response.json()
            assert response.status == 200
            assert original["text"] == "An actual input"
            repeated = await client.post(root, json={"request_id": "http-text-capture", "text": "An actual input"})
            assert (await repeated.json())["id"] == original["id"]
            listing = await client.get(root)
            assert (await listing.json())["total"] == 1
            payload = route_payload(original, key="http-route-request")
            routed = await client.post(f"{root}/{original['id']}/route", json=payload)
            assert routed.status == 200
            saved = await routed.json()
            assert saved["revision"] == 2
            assert store.get_item(saved["destination_id"])["content"] == "Reviewed content"
            denied = await isolated.get(f"{root}/{original['id']}")
            assert denied.status == 404
            assert (await (await isolated.get(root)).json())["total"] == 0
            for selector in ("home", "account", "runtime", "provider", "model"):
                invalid = await client.post(root, json={"request_id": "http-invalid-capture", "text": "text", selector: "other"})
                assert invalid.status == 400
                queried = await client.get(f"{root}?{selector}=other")
                assert queried.status == 400
            conflict = await client.post(f"{root}/{original['id']}/route", json=payload | {"request_id": "http-stale-request"})
            assert conflict.status == 409
            detail = await client.get(f"{root}/{original['id']}")
            assert (await detail.json())["text"] == "An actual input"
        store.close()
        other_store.close()
    asyncio.run(journey())


def test_http_audio_allocates_at_bound_store_and_refuses_overrides(tmp_path, monkeypatch):
    async def journey():
        monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "initial-home"))
        save_use_case_settings("stt", {"enabled": False})
        app, store = make_app(tmp_path / "bound.db")
        monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "changed-global-home"))
        root = "/api/capabilities/knowledge/captures"
        async with TestClient(TestServer(app)) as client:
            form = FormData()
            form.add_field("audio", audio_bytes(), filename="voice.wav", content_type="audio/wav")
            response = await client.post(root + "/audio", data=form, headers={"X-Capture-Request-ID": "http-original-audio"})
            assert response.status == 409
            assert not (tmp_path / "changed-global-home").exists()
            assert not (tmp_path / "files").exists()
            monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "initial-home"))
            form = FormData()
            form.add_field("audio", audio_bytes(), filename="voice.wav", content_type="audio/wav")
            response = await client.post(root + "/audio", data=form, headers={"X-Capture-Request-ID": "http-original-audio"})
            assert response.status == 200
            captured = await response.json()
            item = store.get_item(captured["audio_item_id"])
            from pathlib import Path
            assert Path(item["file_path"]).parent == tmp_path / "files"
            assert Path(item["file_path"]).read_bytes() == audio_bytes()
            endpoint = f"{root}/{captured['id']}/transcribe"
            invalid = await client.post(endpoint, json={"model": "other"})
            assert invalid.status == 400
            actual = await client.post(endpoint)
            assert actual.status == 200
            result = await actual.json()
            assert result["status"] == "transcription_unavailable"
            assert result["transcript"] is None
            assert result["audio_sha256"] == captured["audio_sha256"]
            assert not (tmp_path / "changed-global-home" / "workspace" / "knowledge").exists()
        store.close()
    asyncio.run(journey())
