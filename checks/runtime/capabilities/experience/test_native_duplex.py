import asyncio
import sqlite3
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.workspace.capabilities.experience.native_duplex import NativeDuplex
from gideon.workspace.capabilities.experience.native_duplex_http import (
    register_native_duplex,
)
from gideon.workspace.capabilities.experience.store import (
    Conflict,
    ExperienceStore,
    NotFound,
)


@pytest.fixture
def store(tmp_path):
    value = ExperienceStore(tmp_path)
    with value.connection() as db:
        db.execute(
            "CREATE TABLE native_call_requests(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,command TEXT,state TEXT,result TEXT,created_at REAL)"
        )
        db.execute(
            "INSERT INTO native_call_requests VALUES(?,?,?,?,?,?)",
            ("a" * 32, "call-1", "call", "requested", "requested", 1),
        )
        db.execute(
            "INSERT INTO native_call_requests VALUES(?,?,?,?,?,?)",
            ("b" * 32, "probe-1", "probe", "idle", "idle", 1),
        )
        db.execute(
            "INSERT INTO native_call_requests VALUES(?,?,?,?,?,?)",
            ("c" * 32, "failed-1", "call", "failed", "failure", 1),
        )
    return value


@pytest.fixture
def duplex(store):
    return NativeDuplex(store, platform="linux", xcrun=None)


def start_body(**patch):
    return {
        "call_id": "a" * 32,
        "request_id": "duplex-1",
        "max_turns": 10,
        "capture_seconds": 8,
        **patch,
    }


def test_readiness_is_honest_off_mac_and_names_real_bridges(duplex):
    report = duplex.readiness()
    assert report == {
        "available": False,
        "errors": ["Native duplex audio requires macOS", "Apple xcrun is unavailable"],
        "capture": "AVFoundation microphone PCM",
        "playback": "AVFoundation audio player",
        "stt": "configured Gideon STT",
        "tts": "configured Gideon TTS",
        "device_qualification": "unverified",
    }
    assert duplex.source.is_file()


def test_mac_command_is_fixed_to_bundled_source_and_bounded_arguments(store, tmp_path):
    service = NativeDuplex(store, platform="darwin", xcrun="/usr/bin/xcrun")
    target = tmp_path / "capture.wav"
    assert service.readiness()["available"] is True
    assert service.command("capture", target, 8) == [
        "/usr/bin/xcrun",
        "swift",
        str(service.source),
        "capture",
        str(target),
        "8",
    ]
    assert service.command("play", target) == [
        "/usr/bin/xcrun",
        "swift",
        str(service.source),
        "play",
        str(target),
    ]
    assert service.source.name == "native_duplex.swift"


def test_swift_source_uses_real_permission_capture_and_playback_contract(duplex):
    source = duplex.source.read_text()
    assert "import AVFoundation" in source
    assert "AVCaptureDevice.requestAccess(for: .audio)" in source
    assert "AVAudioRecorder" in source
    assert "kAudioFormatLinearPCM" in source
    assert "AVSampleRateKey: 16000.0" in source
    assert "AVNumberOfChannelsKey: 1" in source
    assert "AVAudioPlayer" in source
    assert "player.isPlaying" in source
    assert "Process(" not in source
    assert "URLSession" not in source


@pytest.mark.parametrize(
    "patch,message",
    [
        ({"request_id": ""}, "request identifier"),
        ({"request_id": "bad space"}, "request identifier"),
        ({"max_turns": 0}, "max_turns"),
        ({"max_turns": 21}, "max_turns"),
        ({"max_turns": True}, "max_turns"),
        ({"capture_seconds": 0}, "capture_seconds"),
        ({"capture_seconds": 31}, "capture_seconds"),
        ({"capture_seconds": True}, "capture_seconds"),
    ],
)
def test_start_rejects_unbounded_values(duplex, patch, message):
    with pytest.raises(ValueError, match=message):
        duplex.start(start_body(**patch))
    assert duplex.list() == []


def test_start_rejects_unknown_fields_and_missing_fields(duplex):
    for body in ({}, {"call_id": "a" * 32}, {**start_body(), "command": "/bin/sh"}):
        with pytest.raises(ValueError, match="Expected call_id"):
            duplex.start(body)
    assert duplex.list() == []


def test_start_requires_existing_nonterminal_nonprobe_call(duplex):
    with pytest.raises(NotFound, match="call request not found"):
        duplex.start(start_body(call_id="d" * 32))
    with pytest.raises(Conflict, match="never initiates"):
        duplex.start(start_body(call_id="b" * 32))
    with pytest.raises(Conflict, match="never initiates"):
        duplex.start(start_body(call_id="c" * 32))
    assert duplex.list() == []


def test_start_persists_honest_unavailable_session_and_replays(duplex, store):
    first = duplex.start(start_body())
    assert first["state"] == "unavailable"
    assert first["revision"] == 1
    assert first["turns"] == []
    assert "requires macOS" in first["error"]
    assert duplex.start(start_body()) == first
    reopened = NativeDuplex(store, platform="linux", xcrun=None)
    assert reopened.get(first["id"]) == first
    assert reopened.list() == [first]


def test_start_request_id_conflict_preserves_original(duplex):
    first = duplex.start(start_body())
    with pytest.raises(Conflict, match="another duplex session"):
        duplex.start(start_body(max_turns=2))
    assert duplex.list() == [first]


def test_get_rejects_invalid_or_missing_identifiers(duplex):
    with pytest.raises(ValueError, match="identifier"):
        duplex.get("../escape")
    with pytest.raises(NotFound, match="not found"):
        duplex.get("d" * 32)


def test_compare_and_swap_tracks_state_and_refuses_stale_updates(store):
    service = NativeDuplex(store, platform="darwin", xcrun="/usr/bin/xcrun")
    session = service.start(start_body())
    stopped = service.stop(session["id"], session["revision"])
    assert stopped["state"] == "stopped"
    assert stopped["revision"] == 2
    with pytest.raises(Conflict, match="revision changed"):
        service.stop(session["id"], session["revision"])


def test_restart_marks_inflight_device_state_interrupted(store):
    service = NativeDuplex(store, platform="darwin", xcrun="/usr/bin/xcrun")
    session = service.start(start_body())
    active = service._update(session["id"], session["revision"], state="capturing")
    reopened = NativeDuplex(store, platform="darwin", xcrun="/usr/bin/xcrun")
    recovered = reopened.get(active["id"])
    assert recovered["state"] == "interrupted"
    assert recovered["revision"] == 3
    assert "actual device state is unknown" in recovered["error"]


def test_stop_refuses_active_device_operation(store):
    service = NativeDuplex(store, platform="darwin", xcrun="/usr/bin/xcrun")
    session = service.start(start_body())
    active = service._update(session["id"], session["revision"], state="speaking")
    with pytest.raises(Conflict, match="interrupt the worker"):
        service.stop(active["id"], active["revision"])


@pytest.mark.asyncio
async def test_subprocess_driver_requires_exact_success_marker(duplex):
    await duplex._run(["/usr/bin/printf", "ok\n"], 2)
    with pytest.raises(Conflict, match="invalid result"):
        await duplex._run(["/usr/bin/printf", "unexpected\n"], 2)
    with pytest.raises(Conflict, match="helper failed"):
        await duplex._run(["/usr/bin/false"], 2)


@pytest.mark.asyncio
async def test_capture_refuses_unavailable_session_without_touching_device(duplex):
    session = duplex.start(start_body())
    with pytest.raises(Conflict, match="not ready"):
        await duplex.capture(session["id"], session["revision"])
    assert duplex.get(session["id"])["state"] == "unavailable"


@pytest.mark.asyncio
async def test_speak_requires_a_captured_turn_and_bounded_reply(store):
    service = NativeDuplex(store, platform="darwin", xcrun="/usr/bin/xcrun")
    session = service.start(start_body())
    with pytest.raises(ValueError, match="Reply text"):
        await service.speak(session["id"], session["revision"], "")
    with pytest.raises(ValueError, match="Reply text"):
        await service.speak(session["id"], session["revision"], "x" * 10001)
    with pytest.raises(Conflict, match="no captured turn"):
        await service.speak(session["id"], session["revision"], "hello")


@pytest.mark.asyncio
async def test_http_exposes_readiness_durable_attach_and_fixed_actions(store):
    app = web.Application()
    register_native_duplex(app, store)
    async with TestClient(TestServer(app)) as client:
        snapshot = await client.get("/api/capabilities/experience/native-duplex")
        assert snapshot.status == 200
        value = await snapshot.json()
        assert value["readiness"]["available"] is False
        assert value["sessions"] == []
        created = await client.post(
            "/api/capabilities/experience/native-duplex", json=start_body()
        )
        assert created.status == 200
        session = await created.json()
        loaded = await client.get(
            "/api/capabilities/experience/native-duplex/" + session["id"]
        )
        assert loaded.status == 200
        assert (await loaded.json())["call_id"] == "a" * 32
        capture = await client.post(
            f"/api/capabilities/experience/native-duplex/{session['id']}/capture",
            json={"revision": session["revision"]},
        )
        assert capture.status == 409
        unknown = await client.post(
            f"/api/capabilities/experience/native-duplex/{session['id']}/shell",
            json={"revision": session["revision"]},
        )
        assert unknown.status == 404


def test_no_host_command_or_external_call_fields_are_stored(duplex):
    session = duplex.start(start_body())
    assert set(session) == {
        "id",
        "request_id",
        "call_id",
        "state",
        "revision",
        "max_turns",
        "capture_seconds",
        "turns",
        "error",
        "created_at",
        "updated_at",
    }
    encoded = str(session).lower()
    assert "executable" not in encoded
    assert "hostname" not in encoded
    assert "dial" not in encoded
    assert "approval" not in encoded
