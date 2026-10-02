from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from gideon.hypermid.adapter import HypermidAdapter
from gideon.hypermid.client import HypermidClient
from gideon.hypermid.models import Scope, Trace


def _daemon_binary() -> Path:
    candidates: list[Path] = []
    explicit = os.environ.get("HYPERMID_DAEMON_BIN")
    if explicit:
        candidates.append(Path(explicit))
    target = os.environ.get("CARGO_TARGET_DIR")
    if target:
        candidates.append(Path(target) / "debug" / "hypermid-daemon")
    candidates.append(Path("target/debug/hypermid-daemon"))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise AssertionError("focused gate requires the built hypermid-daemon binary")


@pytest.fixture
def live_daemon(tmp_path: Path) -> Iterator[Path]:
    socket_path = tmp_path / "run" / "hypermid.sock"
    record_path = tmp_path / "state" / "connection.json"
    enrollment_expires_ms = int(time.time() * 1000) + 120_000
    process = subprocess.Popen(
        [
            str(_daemon_binary()),
            "--socket",
            str(socket_path),
            "--connection-record",
            str(record_path),
            "--local-credential-id",
            "credential-adapter-test",
            "--local-owner-id",
            "owner-test",
            "--local-project-id",
            "project-test",
            "--local-workspace-id",
            "workspace-test",
            "--local-capability-id",
            "capability-adapter-test",
            "--local-capability-operation",
            "read",
            "--local-capability-resource",
            "memory-service",
            "--local-capability-expires-ms",
            str(enrollment_expires_ms),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not record_path.is_file():
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(
                f"hypermid-daemon exited before readiness ({process.returncode}): {stderr}"
            )
        if time.monotonic() >= deadline:
            process.terminate()
            process.wait(timeout=5)
            raise AssertionError("hypermid-daemon connection record timed out")
        time.sleep(0.01)
    try:
        yield record_path
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.asyncio
async def test_real_daemon_authenticated_passthrough_is_authority_preserving(
    live_daemon: Path,
) -> None:
    scope = Scope("owner-test", "project-test", "workspace-test")
    client = HypermidClient(live_daemon, scope=scope)
    adapter = HypermidAdapter(client, mode="pass_through")

    status = await adapter.start()
    assert status.available is True
    assert status.scope_bound is True
    assert status.writer == "gideon"
    assert status.protocol_version == "hypermid.v1"
    assert "passthrough" in status.capabilities

    payload = {
        "messages": [
            {"id": "message-1", "role": "user", "content": "exact bytes: \u2603"}
        ],
        "ordinal": 7,
    }
    trace = Trace("trace-adapter", "request-adapter")
    assert await adapter.passthrough(payload, trace=trace) == payload

    serialized_status = json.dumps(adapter.status().to_dict(), sort_keys=True)
    record = json.loads(live_daemon.read_text(encoding="utf-8"))
    assert record["secret_b64"] not in serialized_status
    assert "client_private_key" not in serialized_status
    await adapter.stop()
