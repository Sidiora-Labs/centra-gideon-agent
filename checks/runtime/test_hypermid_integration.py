from __future__ import annotations

import os
from pathlib import Path

import pytest

from gideon.cognition.context_engine import DefaultContextEngine, get_engine, set_engine
from gideon.cognition.history import ConversationLog
from gideon.hypermid.adapter import HypermidAdapter
from gideon.hypermid.client import HypermidClient
from gideon.hypermid.config import (
    DaemonConfig,
    DaemonTransport,
    LocalAuthConfig,
    LocalAuthMethod,
)
from gideon.hypermid.lifecycle import HypermidLifecycle
from gideon.hypermid.models import Scope


def _daemon_binary() -> Path:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured:
        candidate = Path(configured)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    target = os.environ.get("CARGO_TARGET_DIR")
    candidates = [Path(target) / "debug" / "hypermid-daemon"] if target else []
    candidates.append(Path("target/debug/hypermid-daemon"))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise AssertionError("focused gate requires the built hypermid-daemon binary")


@pytest.mark.asyncio
async def test_runtime_lifecycle_keeps_conversation_log_authoritative(tmp_path) -> None:
    run_dir = tmp_path / "runtime"
    socket_path = run_dir / "hypermid.sock"
    record_path = run_dir / "connection.json"
    config = DaemonConfig(
        transport=DaemonTransport.UNIX_SOCKET,
        endpoint=str(socket_path),
        executable=str(_daemon_binary()),
        start_on_demand=True,
        auth=LocalAuthConfig(
            method=LocalAuthMethod.PEER_AND_HMAC,
            token_file=str(run_dir / "auth.token"),
            require_peer_identity=True,
        ),
    ).with_connection_record(str(record_path))
    client = HypermidClient(
        record_path,
        scope=Scope("owner-runtime", "project-runtime", "workspace-runtime"),
    )
    adapter = HypermidAdapter(client, mode="pass_through")
    lifecycle = HypermidLifecycle(
        adapter,
        config,
        connection_record=config.connection_record_path,
    )
    log = ConversationLog(tmp_path / "sessions")
    log.init()
    log.append("session-1", "user", "preserve this exact transcript")
    transcript_path = log._path("session-1")
    before = transcript_path.read_bytes()

    try:
        status = await lifecycle.start()
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": "byte-preserving pass-through: café 界",
                }
            ]
        }
        assert status.available is True
        assert status.healthy is False
        assert status.scope_bound is True
        assert status.writer == "gideon"
        assert status.protocol_version == "hypermid.v1"
        assert get_engine() is adapter
        assert await adapter.passthrough(payload) == payload
        assert transcript_path.read_bytes() == before
    finally:
        await lifecycle.stop()
        set_engine(None)

    assert isinstance(get_engine(), DefaultContextEngine)
    assert not socket_path.exists()
    assert not record_path.exists()
    assert not list(run_dir.glob("tls-*"))
