import base64
import hashlib
import hmac
import json
import sqlite3

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.workspace.capabilities.experience.native_duplex_helper import (
    DuplexExecutorError,
    NativeDuplexExecutor,
    register_native_duplex_executor,
)

TOKEN = "executor-pair-" + ("x" * 32)
SESSION = "session-one"
OPERATION = "operation-one"


def capture(**patch):
    return {
        "session_id": SESSION,
        "operation_id": OPERATION,
        "capture_seconds": 8,
        **patch,
    }


def wave():
    return (
        b"RIFF"
        + (36).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + b"\x01\x00\x01\x00"
        + (16000).to_bytes(4, "little")
        + (32000).to_bytes(4, "little")
        + b"\x02\x00\x10\x00data"
        + (4).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
    )


def playback(**patch):
    audio = wave()
    return {
        "session_id": SESSION,
        "operation_id": OPERATION,
        "mime": "audio/wav",
        "audio": base64.b64encode(audio).decode(),
        "sha256": hashlib.sha256(audio).hexdigest(),
        **patch,
    }


@pytest.fixture
def executor(tmp_path):
    return NativeDuplexExecutor(root=tmp_path, platform="linux", xcrun=None)


def test_readiness_is_truthful_and_source_is_fixed_avfoundation(executor):
    report = executor.readiness()
    assert report["available"] is False
    assert report["errors"] == [
        "Paired duplex execution requires macOS",
        "Apple xcrun is unavailable",
    ]
    assert report["permission"] == "microphone requested by AVFoundation"
    source = executor.source.read_text()
    for value in [
        "AVCaptureDevice.requestAccess(for: .audio)",
        "AVAudioRecorder",
        "AVAudioPlayer",
        "AVSampleRateKey: 16000.0",
        "AVNumberOfChannelsKey: 1",
    ]:
        assert value in source
    assert "Process(" not in source and "URLSession" not in source


@pytest.mark.parametrize(
    "body",
    [
        {},
        {
            "session_id": SESSION,
            "operation_id": OPERATION,
            "capture_seconds": 8,
            "command": "/bin/sh",
        },
        capture(session_id="../escape"),
        capture(operation_id="bad space"),
        capture(capture_seconds=0),
        capture(capture_seconds=31),
        capture(capture_seconds=True),
    ],
)
def test_capture_schema_rejects_commands_paths_and_unbounded_values(executor, body):
    with pytest.raises(DuplexExecutorError) as error:
        executor.fields(body, "capture")
    assert error.value.status == 400


@pytest.mark.parametrize(
    "patch",
    [
        {"mime": "audio/mpeg"},
        {"audio": "not-base64"},
        {"sha256": "0" * 64},
        {"model": "override"},
        {"provider": "override"},
        {"endpoint": "https://override"},
    ],
)
def test_play_schema_accepts_only_digest_bound_wav(executor, patch):
    with pytest.raises(DuplexExecutorError) as error:
        executor.fields(playback(**patch), "play")
    assert error.value.status == 400
    assert executor.fields(playback(), "play") == wave()


async def test_actual_linux_dispatch_fails_without_claiming_capture(executor):
    with pytest.raises(DuplexExecutorError, match="requires macOS"):
        await executor.capture(capture())
    with sqlite3.connect(executor.db) as database:
        row = database.execute(
            "SELECT kind,status,result FROM operations WHERE id=?", (OPERATION,)
        ).fetchone()
    assert row[0] == "capture" and row[1] == "failed"
    assert "macOS" in row[2]
    with pytest.raises(DuplexExecutorError, match="cannot be replayed safely"):
        await executor.capture(capture())


def test_operation_conflicts_and_completed_play_receipt_is_idempotent(executor):
    body = playback()
    assert executor.reserve(body, "play") is None
    result = {"status": "played", "sha256": body["sha256"]}
    executor.finish(OPERATION, "completed", result)
    assert executor.reserve(body, "play") == result
    with pytest.raises(DuplexExecutorError, match="conflicts"):
        executor.reserve({**body, "sha256": "1" * 64}, "play")


def test_restart_marks_running_device_operation_uncertain(tmp_path):
    first = NativeDuplexExecutor(root=tmp_path, platform="linux", xcrun=None)
    first.reserve(capture(), "capture")
    reopened = NativeDuplexExecutor(root=tmp_path, platform="linux", xcrun=None)
    with sqlite3.connect(reopened.db) as database:
        status, result = database.execute(
            "SELECT status,result FROM operations WHERE id=?", (OPERATION,)
        ).fetchone()
    assert status == "uncertain"
    assert "actual microphone or speaker state is unknown" in result


async def test_authenticated_http_protocol_and_fixed_routes(executor):
    @web.middleware
    async def authenticate(request, handler):
        if not hmac.compare_digest(
            request.headers.get("Authorization", ""), "Bearer " + TOKEN
        ):
            return web.json_response(
                {"error": "Native authentication required"}, status=401
            )
        return await handler(request)

    app = web.Application(middlewares=[authenticate], client_max_size=3 * 1024 * 1024)
    register_native_duplex_executor(app, executor)
    async with TestClient(TestServer(app)) as client:
        assert (
            await client.post("/v1/voice/duplex/capture", json=capture())
        ).status == 401
        headers = {"Authorization": "Bearer " + TOKEN}
        response = await client.post(
            "/v1/voice/duplex/capture", json=capture(), headers=headers
        )
        assert response.status == 503
        assert "requires macOS" in (await response.json())["error"]
        wrong = await client.post(
            "/v1/voice/duplex/shell",
            json={"command": "touch forbidden"},
            headers=headers,
        )
        assert wrong.status == 404
        invalid = await client.post(
            "/v1/voice/duplex/play",
            json={**playback(operation_id="second"), "path": "/tmp/audio"},
            headers=headers,
        )
        assert invalid.status == 400
        assert not (executor.root / "forbidden").exists()


def test_executor_persists_no_audio_or_pairing_secret(executor):
    body = playback()
    executor.reserve(body, "play")
    raw = executor.db.read_bytes()
    assert wave() not in raw
    assert body["audio"].encode() not in raw
    assert TOKEN.encode() not in raw
    assert executor.db.stat().st_mode & 0o777 == 0o600


def test_capture_and_playback_replay_rules_survive_restart(tmp_path):
    first = NativeDuplexExecutor(root=tmp_path, platform="linux", xcrun=None)
    capture_body = capture(operation_id="capture-finished")
    first.reserve(capture_body, "capture")
    first.finish(
        capture_body["operation_id"], "completed_no_replay", {"sha256": "1" * 64}
    )
    play_body = playback(operation_id="play-finished")
    first.reserve(play_body, "play")
    receipt = {"status": "played", "sha256": play_body["sha256"]}
    first.finish(play_body["operation_id"], "completed", receipt)
    reopened = NativeDuplexExecutor(root=tmp_path, platform="linux", xcrun=None)
    with pytest.raises(
        DuplexExecutorError, match="cannot be replayed safely"
    ) as capture_replay:
        reopened.reserve(capture_body, "capture")
    assert capture_replay.value.status == 409
    assert reopened.reserve(play_body, "play") == receipt


def test_storage_refuses_symlinked_operation_database(tmp_path):
    root = tmp_path / "capabilities/experience/native-duplex-executor"
    root.mkdir(parents=True)
    target = tmp_path / "outside.sqlite3"
    target.write_bytes(b"outside")
    (root / "operations.sqlite3").symlink_to(target)
    with pytest.raises(ValueError, match="symlink refused"):
        NativeDuplexExecutor(root=tmp_path, platform="linux", xcrun=None)
    assert target.read_bytes() == b"outside"


def test_playback_decoded_size_limit_precedes_any_device_operation(executor):
    audio = b"R" * (2 * 1024 * 1024 + 1)
    body = playback(
        operation_id="oversized-play",
        audio=base64.b64encode(audio).decode(),
        sha256=hashlib.sha256(audio).hexdigest(),
    )
    with pytest.raises(DuplexExecutorError, match="digest") as rejected:
        executor.fields(body, "play")
    assert rejected.value.status == 400
    with sqlite3.connect(executor.db) as database:
        assert database.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
