"""Attended CLI against native loopback gateway routes in an isolated home."""

import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_SERVER = r"""
import asyncio, sys
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine import gateway_base
from gideon.interfaces.dashboard.server import start_dashboard
async def main():
    claim = gateway_base.claim_home()
    port = int(sys.argv[1])
    cfg = AppConfig.load()
    sessions = ConversationDirectory(cfg, provider_factory=cfg.create_provider_factory())
    runner, state = await start_dashboard(sessions, port=port, local_only=True, configured_host="127.0.0.1")
    gateway_base.publish(port)
    print("LOCAL_GATEWAY_READY", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        gateway_base.unpublish()
        claim.close()
asyncio.run(main())
"""


def test_actual_attended_cli_uses_home_gateway(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    playback = tmp_path / "playback.json"

    def script(turns):
        playback.write_text(
            json.dumps({"version": 1, "on_exhausted": "error", "turns": turns})
        )

    script([{"text": "Actual gateway fixture answer.", "stop_reason": "end_turn"}])
    (home / "active_models.json").write_text(
        json.dumps({"chat": ["Scripted:scripted-1"]})
    )
    monkeypatch.setenv("GIDEON_HOME", str(home))
    (home / "config.json").write_text(
        json.dumps(
            {
                "agent": {"provider": "native", "approval_mode": "interactive"},
                "session": {"pool_size": 0},
                "dashboard": {"restore_sessions": False},
                "companion": {"enabled": False},
                "durability": {"enabled": False},
                "default_agent": "local",
                "agents": {
                    "local": {
                        "provider": "native",
                        "default_dir": str(workspace),
                        "approval_mode": "interactive",
                    }
                },
                "providers": [
                    {"name": "Scripted", "type": "scripted", "model": "scripted-1"}
                ],
            }
        )
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(word in key for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
    }
    env.update(
        GIDEON_HOME=str(home),
        GIDEON_AUTH_MODE="local_token",
        GIDEON_PORT=str(port),
        GIDEON_PROJECT_DIR=str(tmp_path),
        GIDEON_WORKSPACE=str(workspace),
        HTTP_PROXY="http://127.0.0.1:1",
        http_proxy="http://127.0.0.1:1",
        NO_PROXY="",
        no_proxy="",
    )
    runtime = str(Path(__file__).resolve().parents[2] / "runtime")
    env["PYTHONPATH"] = runtime
    env["GIDEON_SCRIPTED_MODEL_SCRIPT"] = str(playback)
    log = tmp_path / "gateway.log"
    with log.open("w") as errors:
        server = subprocess.Popen(
            [sys.executable, "-u", "-c", _SERVER, str(port)],
            stdout=subprocess.PIPE,
            stderr=errors,
            env=env,
            cwd=tmp_path,
            text=True,
        )
        try:
            ready, _, _ = select.select([server.stdout], [], [], 75)
            assert ready, log.read_text()[-3000:]
            assert (
                server.stdout.readline().strip() == "LOCAL_GATEWAY_READY"
            ), log.read_text()[-3000:]
            command = [sys.executable, "-m", "gideon", "chat", "--port", str(port)]
            blank = subprocess.run(
                [*command, "-m", "  "],
                env=env,
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert blank.returncode == 2, blank.stderr
            assert "non-empty" in blank.stderr
            quit_chat = subprocess.run(
                command,
                input="exit\n",
                env=env,
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert quit_chat.returncode == 0, quit_chat.stderr
            from urllib.parse import quote

            from gideon.interfaces.cli.run import _api, mint_local_token

            token = mint_local_token(port)
            assert _api(port, token, "/api/chat/sessions")["result"] == []
            turn = subprocess.run(
                [
                    *command,
                    "--model",
                    "Scripted:scripted-1",
                    "-m",
                    "local attended gateway proof",
                ],
                env=env,
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert turn.returncode == 0, (
                turn.stdout + turn.stderr + log.read_text()[-4000:]
            )
            assert "Actual gateway fixture answer." in turn.stdout
            sessions = _api(port, token, "/api/chat/sessions")["result"]
            assert len(sessions) == 1
            key = sessions[0]["key"]
            assert not key.startswith("inbound:cli:")
            detail = _api(port, token, f"/api/chat/sessions/{quote(key, safe='')}")
            assert "scripted-1" in detail["model"], detail
            user = next(row for row in detail["messages"] if row["role"] == "user")
            assert user["meta"]["ingress"]["principal"]["kind"] == "owner"

            def await_approval(process):
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    pending = _api(port, token, "/api/approvals")["result"]
                    if pending:
                        return pending[0]
                    if process.poll() is not None:
                        out, err = process.communicate()
                        raise AssertionError(out + err + log.read_text()[-4000:])
                    time.sleep(0.1)
                raise AssertionError(
                    "native gateway never registered approval: "
                    + log.read_text()[-4000:]
                )

            approved_path = workspace / "approved.txt"
            script(
                [
                    {
                        "tool_calls": [
                            {
                                "id": "write-approved",
                                "name": "write_file",
                                "input": {
                                    "path": str(approved_path),
                                    "content": "approved native execution",
                                },
                            }
                        ],
                        "stop_reason": "tool_calls",
                    },
                    {"text": "Approved tool completed.", "stop_reason": "end_turn"},
                ]
            )
            approved = subprocess.Popen(
                [*command, "-m", "approve native write"],
                env=env,
                cwd=tmp_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                pending = await_approval(approved)
                assert not approved_path.exists()
                _api(
                    port,
                    token,
                    f"/api/approvals/{pending['id']}/approve",
                    {"expected_revision": pending["revision"]},
                )
                output, errors = approved.communicate(timeout=60)
                assert approved.returncode == 0, (
                    output + errors + log.read_text()[-4000:]
                )
                rows = _api(port, token, "/api/chat/sessions")["result"]
                approved_key = next(row["key"] for row in rows if row["key"] != key)
                approved_detail = _api(
                    port, token, f"/api/chat/sessions/{quote(approved_key, safe='')}"
                )
                assert approved_path.exists(), (
                    output + errors + json.dumps(approved_detail)
                )
                assert approved_path.read_text() == "approved native execution"
                assert "waiting for your decision" in errors
                assert "Approved:" in errors
                assert "Approved tool completed." in output
            finally:
                if approved.poll() is None:
                    approved.kill()
                    approved.wait(timeout=5)

            cancelled_path = workspace / "cancelled.txt"
            script(
                [
                    {
                        "tool_calls": [
                            {
                                "id": "write-cancelled",
                                "name": "write_file",
                                "input": {
                                    "path": str(cancelled_path),
                                    "content": "must not execute",
                                },
                            }
                        ],
                        "stop_reason": "tool_calls",
                    }
                ]
            )
            cancelled = subprocess.Popen(
                [*command, "-m", "cancel native write"],
                env=env,
                cwd=tmp_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                pending = await_approval(cancelled)
                assert not cancelled_path.exists()
                cancelled.send_signal(signal.SIGINT)
                output, errors = cancelled.communicate(timeout=60)
                assert cancelled.returncode == 1, (
                    output + errors + log.read_text()[-4000:]
                )
                assert "Stopping the turn." in errors
                assert "stopped before it finished" in errors
                assert not cancelled_path.exists()
                assert _api(port, token, "/api/approvals")["result"] == []
            finally:
                if cancelled.poll() is None:
                    cancelled.kill()
                    cancelled.wait(timeout=5)
            other = tmp_path / "other"
            other.mkdir()
            wrong_env = {**env, "GIDEON_HOME": str(other)}
            refused = subprocess.run(
                [*command, "-m", "must not reach chat"],
                env=wrong_env,
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert refused.returncode == 1
            assert "another home" in refused.stderr, refused.stderr
            assert not (other / ".local_secret").exists()
        finally:
            server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
