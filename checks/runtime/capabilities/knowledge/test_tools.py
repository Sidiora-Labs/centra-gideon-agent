"""Native tool invocation using actual runtime services and persistent stores."""

import asyncio
import importlib
import json
from pathlib import Path

import pytest

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.engine import session_restrictions
from gideon.engine.session import ConversationDirectory
from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.providers.use_cases import save_use_case_settings
from gideon.integrations.action_providers.services import ActionServices, get_action_services, set_action_services
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.integrations.tool_providers.base import ToolProvider, RiskLevel
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession
from gideon.workspace.capabilities.knowledge.tools import KnowledgeCapabilityTools, create_provider


@pytest.fixture
def runtime(tmp_path):
    previous = get_action_services()
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = store
    services = ActionServices(state=state, spawn_background=asyncio.create_task)
    set_action_services(services)
    token = set_current_session_key("dashboard:tool-test")
    yield services
    reset_current_session_key(token)
    set_action_services(previous)
    store.close()


def invoke(provider, name, arguments):
    return asyncio.run(provider.invoke(name, arguments))


def test_manifest_loads_real_native_provider(runtime):
    path = Path(__file__).parents[4] / "runtime/gideon/extensions/apps/native/gideon-personal-knowledge/app.json"
    manifest = AppManifest.from_json_file(path)
    assert manifest.name == "gideon-personal-knowledge"
    definition = json.loads(path.read_text())
    module, function = definition["provider"]["implementation"].split(":")
    provider = getattr(importlib.import_module(module), function)()
    assert isinstance(provider, ToolProvider)
    assert provider.name == "gideon-personal-knowledge"
    assert provider.connected
    assert provider.info()["display_name"] == "Personal Knowledge"


def test_discovery_declares_risk_and_strict_input_shapes(runtime):
    provider = create_provider()
    tools = asyncio.run(provider.list_tools())
    assert len(tools) == 38
    assert len({tool.name for tool in tools}) == len(tools)
    by_name = {tool.name: tool for tool in tools}
    for name in ("knowledge_anniversaries", "knowledge_anniversary_source", "knowledge_capture_list", "knowledge_capture_get"):
        assert by_name[name].risk_level == RiskLevel.SAFE
        assert not by_name[name].requires_approval
    for name in ("knowledge_capture_text", "knowledge_capture_route", "knowledge_capture_transcribe"):
        assert by_name[name].risk_level == RiskLevel.CAUTION
        assert by_name[name].requires_approval
    for tool in tools:
        assert tool.parameters["additionalProperties"] is False
        assert "home" not in tool.parameters["properties"]
        assert "provider" not in tool.parameters["properties"]
        assert tool.provider == provider.name


def test_native_video_review_import_and_exact_transcript(runtime):
    provider = create_provider()
    body = {"url": "https://youtu.be/dQw4w9WgXcQ", "title": "Native video source", "format": "vtt", "content": "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nGrounded segment\n", "language": "en"}
    reviewed = invoke(provider, "knowledge_video_preview", body)
    assert reviewed.success
    preview = json.loads(reviewed.output)
    assert preview["segments"][0]["source_link"].endswith("&t=1s")
    imported = invoke(provider, "knowledge_video_import", {"request_id": "native-video-request", **body, "preview_id": preview["preview_id"]})
    assert imported.success
    job = json.loads(imported.output)
    assert job["status"] == "completed"
    assert job["source_id"] and job["transcript_id"] and job["original_source_id"]
    listing = invoke(provider, "knowledge_video_list", {"limit": 1})
    assert listing.success
    assert json.loads(listing.output)["items"][0]["id"] == job["id"]
    detail = invoke(provider, "knowledge_video_get", {"id": job["id"]})
    assert json.loads(detail.output)["video_id"] == "dQw4w9WgXcQ"
    transcript = invoke(provider, "knowledge_video_transcript", {"id": job["id"]})
    assert transcript.success
    assert "Grounded segment" in json.loads(transcript.output)["content"]
    repeated = invoke(provider, "knowledge_video_import", {"request_id": "native-video-request", **body, "preview_id": preview["preview_id"]})
    assert json.loads(repeated.output) == job


@pytest.mark.parametrize("name,args", [
    ("knowledge_video_preview", {"url": "https://youtu.be/dQw4w9WgXcQ", "title": "Title", "format": "text", "content": "one", "language": "en"}),
    ("knowledge_video_import", {"request_id": "native-video-bad", "url": "https://youtu.be/dQw4w9WgXcQ", "title": "Title", "format": "vtt", "content": "one", "language": "en"}),
    ("knowledge_video_fetch", {"request_id": "native-video-fetch", "url": "https://youtu.be/dQw4w9WgXcQ", "language": "en", "transcript": True, "video": False, "audio": False, "provider": "unsafe"}),
    ("knowledge_video_get", {"id": "one", "home": "/other"}),
    ("knowledge_video_cancel", {}),
    ("knowledge_video_transcript", {"id": "one", "raw": True}),
])
def test_native_video_schemas_refuse_incomplete_or_extra_arguments(runtime, name, args):
    result = invoke(create_provider(), name, args)
    assert not result.success
    assert "Invalid tool arguments" in result.error
    table = runtime.state.knowledge_store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='capability_knowledge_video_jobs'").fetchone()
    assert table is None or runtime.state.knowledge_store.db.execute("SELECT count(*) FROM capability_knowledge_video_jobs").fetchone()[0] == 0


def test_native_anniversary_and_exact_source(runtime):
    store = runtime.state.knowledge_store
    identity = store.create_typed_item(item_type="journal", title="A past day", content="Actual historical journal")
    store.db.execute("UPDATE items SET created_at=? WHERE id=?", ("2024-09-25T23:30:00Z", identity))
    store.db.commit()
    provider = create_provider()
    result = invoke(provider, "knowledge_anniversaries", {"date": "2026-09-26", "timezone": "Europe/Berlin"})
    assert result.success
    data = json.loads(result.output)
    assert data["items"][0]["source_id"] == identity
    assert data["items"][0]["years_ago"] == 2
    source = invoke(provider, "knowledge_anniversary_source", {"source_type": "journal", "source_id": identity})
    assert source.success
    assert json.loads(source.output)["content"] == "Actual historical journal"
    missing = invoke(provider, "knowledge_anniversary_source", {"source_type": "note", "source_id": identity})
    assert not missing.success
    assert missing.metadata["status"] == 404


def test_native_capture_route_replay_and_source_identity(runtime):
    provider = create_provider()
    capture = invoke(provider, "knowledge_capture_text", {"request_id": "native-capture-01", "text": "Actual original capture"})
    assert capture.success
    original = json.loads(capture.output)
    read = invoke(provider, "knowledge_capture_get", {"id": original["id"]})
    assert json.loads(read.output)["text"] == "Actual original capture"
    listing = invoke(provider, "knowledge_capture_list", {"limit": 1})
    assert json.loads(listing.output)["total"] == 1
    payload = {"id": original["id"], "request_id": "native-route-001", "revision": 1, "destination": "fleeting", "title": "Reviewed idea", "content": "Reviewed original"}
    routed = invoke(provider, "knowledge_capture_route", payload)
    repeated = invoke(provider, "knowledge_capture_route", payload)
    assert routed.success and repeated.success
    saved = json.loads(routed.output)
    assert json.loads(repeated.output) == saved
    assert saved["text"] == "Actual original capture"
    assert saved["revision"] == 2
    assert runtime.state.knowledge_store.get_item(saved["destination_id"])["content"] == "Reviewed original"
    assert runtime.state.knowledge_store.db.execute("SELECT count(*) FROM items").fetchone()[0] == 1


@pytest.mark.parametrize("name,args", [
    ("knowledge_anniversaries", {"home": "/other"}),
    ("knowledge_capture_list", {"limit": 101}),
    ("knowledge_capture_list", {"limit": True}),
    ("knowledge_capture_get", {"id": "a", "account": "another"}),
    ("knowledge_capture_text", {"request_id": "native-invalid", "text": "x", "model": "other"}),
    ("knowledge_capture_transcribe", {"id": "a", "provider": "other"}),
    ("knowledge_anniversary_source", {"source_type": "unknown", "source_id": "a"}),
])
def test_tool_schema_refuses_selectors_before_access(runtime, name, args):
    result = invoke(create_provider(), name, args)
    assert not result.success
    assert "Invalid tool arguments" in result.error
    assert runtime.state.knowledge_store.db.execute("SELECT count(*) FROM items").fetchone()[0] == 0


def test_missing_session_never_becomes_dashboard_authority(runtime):
    provider = create_provider()
    token = set_current_session_key("")
    try:
        result = invoke(provider, "knowledge_capture_list", {})
        assert not result.success
        assert result.metadata["status"] == 403
        assert "active session" in result.error
    finally:
        reset_current_session_key(token)


def test_temporary_session_cannot_read_or_write(runtime):
    key = "dashboard:temporary-native"
    session_restrictions.mark_temporary(key)
    token = set_current_session_key(key)
    try:
        provider = create_provider()
        read = invoke(provider, "knowledge_anniversaries", {})
        write = invoke(provider, "knowledge_capture_text", {"request_id": "temporary-native", "text": "Must not persist"})
        assert not read.success and not write.success
        assert read.metadata["status"] == write.metadata["status"] == 403
    finally:
        session_restrictions.clear(key)
        reset_current_session_key(token)


def test_incognito_session_can_read_but_cannot_write(runtime):
    key = "dashboard:incognito-native"
    session_restrictions.mark_incognito(key)
    token = set_current_session_key(key)
    try:
        provider = create_provider()
        read = invoke(provider, "knowledge_anniversaries", {})
        write = invoke(provider, "knowledge_capture_text", {"request_id": "incognito-native", "text": "Must not persist"})
        assert read.success
        assert not write.success
        assert write.metadata["status"] == 403
    finally:
        session_restrictions.clear(key)
        reset_current_session_key(token)


def test_actual_chat_session_modes_are_enforced(runtime):
    runtime.state._sessions["tool-test"] = _ChatSession("tool-test", memory_mode="temporary")
    provider = create_provider()
    assert not invoke(provider, "knowledge_capture_list", {}).success
    runtime.state._sessions["tool-test"].memory_mode = "incognito"
    assert invoke(provider, "knowledge_capture_list", {}).success
    denied = invoke(provider, "knowledge_capture_text", {"request_id": "chat-mode-native", "text": "No write"})
    assert not denied.success
    runtime.state._sessions["tool-test"].memory_mode = "persistent"
    assert invoke(provider, "knowledge_capture_text", {"request_id": "chat-mode-native", "text": "Allowed write"}).success


def test_bound_service_store_survives_registry_and_home_changes(runtime, tmp_path, monkeypatch):
    provider = create_provider()
    other_store = KnowledgeStore(str(tmp_path / "other.db"))
    other_state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    other_state._knowledge_store = other_store
    set_action_services(ActionServices(state=other_state, spawn_background=asyncio.create_task))
    before = str(provider._home)
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "unrelated-home"))
    result = invoke(provider, "knowledge_capture_text", {"request_id": "bound-store-native", "text": "Stay in the original store"})
    assert not result.success
    assert result.metadata["status"] == 409
    assert not (tmp_path / "unrelated-home").exists()
    assert runtime.state.knowledge_store.db.execute("SELECT count(*) FROM capability_knowledge_captures").fetchone()[0] == 0
    monkeypatch.setenv("GIDEON_HOME", before)
    result = invoke(provider, "knowledge_capture_text", {"request_id": "bound-store-native", "text": "Stay in the original store"})
    assert result.success
    second = create_provider()
    isolated = invoke(second, "knowledge_capture_list", {})
    assert json.loads(isolated.output)["total"] == 0
    assert not (tmp_path / "unrelated-home" / "workspace" / "knowledge").exists()
    other_store.close()


def test_unbound_startup_is_honest_and_later_registry_binding_works(runtime):
    set_action_services(None)
    provider = create_provider()
    assert not provider.connected
    missing = invoke(provider, "knowledge_capture_list", {})
    assert not missing.success
    assert missing.metadata["status"] == 503
    set_action_services(runtime)
    assert provider.connected
    result = invoke(provider, "knowledge_capture_list", {})
    assert result.success
    assert json.loads(result.output)["total"] == 0


def test_unknown_tool_and_factory_selector_fail_honestly(runtime):
    unknown = invoke(create_provider(), "knowledge_delete_everything", {})
    assert not unknown.success
    assert "Unknown" in unknown.error
    with pytest.raises(ValueError, match="bound runtime"):
        create_provider({"home": "/other"})


def test_provider_redacts_output_without_rewriting_original(runtime):
    secret = "sk-" + "a" * 48
    original = f"My API key is {secret}"
    result = invoke(create_provider(), "knowledge_capture_text", {"request_id": "secret-native-capture", "text": original})
    assert result.success
    assert secret not in result.output
    identity = json.loads(result.output)["id"]
    stored = runtime.state.knowledge_store.db.execute("SELECT original_text FROM capability_knowledge_captures WHERE id=?", (identity,)).fetchone()
    assert stored["original_text"] == original


def test_voice_unavailable_result_keeps_original_receipt(runtime, tmp_path, monkeypatch):
    from gideon.workspace.capabilities.knowledge.capture import CaptureInbox
    import io
    import wave
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(bytes(1600))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "disabled-stt"))
    save_use_case_settings("stt", {"enabled": False})
    inbox = CaptureInbox(runtime.state.knowledge_store)
    capture = inbox.save_audio("native-voice-original", buffer.getvalue(), "recording.wav", "audio/wav")
    result = invoke(create_provider(), "knowledge_capture_transcribe", {"id": capture["id"]})
    assert not result.success
    assert result.recovery_hints
    data = json.loads(result.output)
    assert data["status"] == "transcription_unavailable"
    assert data["audio_item_id"] == capture["audio_item_id"]
    assert data["audio_sha256"] == capture["audio_sha256"]
    assert data["transcript"] is None
