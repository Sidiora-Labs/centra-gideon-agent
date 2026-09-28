from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from gideon.integrations.acp.transport import AcpProcess


def _recording_cli(record: Path) -> str:
    return (
        "import json, os, pathlib; "
        f"pathlib.Path({str(record)!r}).write_text(json.dumps(dict(os.environ)))"
    )


def test_acp_child_is_allowlisted_with_sandbox_auto_and_off(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home / ".gideon"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "planted-provider-sentinel")
    monkeypatch.setenv("ACME_DEPLOY_PAT", "planted-unknown-sentinel")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "planted-cloud-sentinel")
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "agent.sock"))
    monkeypatch.setenv("HTTPS_PROXY", "http://operator:proxy-sentinel@proxy.example.test:3128")
    monkeypatch.setenv("npm_config_registry", "https://npm.example.test/")
    monkeypatch.setenv("npm_config__authToken", "planted-npm-token")

    async def run(mode: str, command: list[str], record: Path) -> dict[str, str]:
        cli = AcpProcess(
            command=command,
            work_dir=tmp_path,
            sandbox_mode=mode,
            sandbox="none",
            session_key="dashboard:test-session",
            extra_env={"CLAUDE_CONFIG_DIR": str(home / "cc-config")},
        )
        await cli.spawn()
        assert cli.process is not None
        await asyncio.wait_for(cli.process.wait(), timeout=15)
        cli.teardown()
        return json.loads(record.read_text(encoding="utf-8"))

    for mode in ("auto", "off"):
        record = tmp_path / f"acp-{mode}.json"
        command = [sys.executable, "-c", _recording_cli(record)]
        seen = asyncio.run(run(mode, command, record))
        assert seen.get("PATH")
        assert seen.get("GIDEON_SESSION_KEY") == "dashboard:test-session"
        assert seen.get("CLAUDE_CONFIG_DIR") == str(home / "cc-config")
        for name in (
            "ANTHROPIC_API_KEY",
            "ACME_DEPLOY_PAT",
            "AWS_SECRET_ACCESS_KEY",
            "SSH_AUTH_SOCK",
            "npm_config__authToken",
        ):
            assert name not in seen
        assert all("proxy-sentinel" not in value for value in seen.values())

    fallback = AcpProcess(
        command=["npx", "-y", "sample-acp"], work_dir=tmp_path, sandbox="none"
    )._environment()
    assert fallback.get("npm_config_registry") == "https://npm.example.test/"
    assert "npm_config__authToken" not in fallback
    binary = AcpProcess(
        command=["/opt/acp/bin/server"], work_dir=tmp_path, sandbox="none"
    )._environment()
    assert "npm_config_registry" not in binary
