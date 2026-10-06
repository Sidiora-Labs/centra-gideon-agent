from __future__ import annotations

import asyncio
import json
import shlex
import sys
from pathlib import Path

from gideon.extensions.apps.app_manager import _run_hook
from gideon.integrations.local_models.sidecar import SidecarInstall
from gideon.integrations.mcp_discovery import McpServerInfo, probe_server
from gideon.security.sandbox import build_child_env

_SENTINELS = {
    "ANTHROPIC_API_KEY": "planted-provider-sentinel",
    "ACME_DEPLOY_PAT": "planted-unknown-sentinel",
    "AWS_SECRET_ACCESS_KEY": "planted-cloud-sentinel",
    "SSH_AUTH_SOCK": "/tmp/planted-agent.sock",
}
_PROXY_SECRET = "planted-proxy-password"


def _plant(monkeypatch, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home / ".gideon"))
    for name, value in _SENTINELS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(
        "HTTPS_PROXY", f"http://operator:{_PROXY_SECRET}@proxy.example.test:3128"
    )
    monkeypatch.setenv(
        "PIP_INDEX_URL", "https://installer:index-secret@pypi.example.test/simple"
    )
    monkeypatch.setenv("PIP_TRUSTED_HOST", "pypi.example.test")
    monkeypatch.setenv("PIP_TARGET", str(home / "elsewhere"))
    monkeypatch.setenv("npm_config_registry", "https://npm.example.test/")
    monkeypatch.setenv("npm_config__authToken", "planted-npm-token")


def _assert_safe(env: dict[str, str]) -> None:
    assert env.get("PATH")
    for name, value in _SENTINELS.items():
        assert name not in env
        assert value not in env.values()
    for value in (_PROXY_SECRET, "index-secret", "planted-npm-token"):
        assert all(value not in item for item in env.values())


def _recorder_script(path: Path) -> str:
    return (
        "import json, os, pathlib; "
        f"pathlib.Path({str(path)!r}).write_text(json.dumps(dict(os.environ)))"
    )


def test_child_builder_keeps_safe_installer_settings_and_strips_logins(
    monkeypatch, tmp_path
):
    _plant(monkeypatch, tmp_path / "home")
    pip_env = build_child_env(site="test-pip", installer="pip")
    npm_env = build_child_env(site="test-npm", installer="npm")
    _assert_safe(pip_env)
    _assert_safe(npm_env)
    assert pip_env["PIP_INDEX_URL"] == "https://pypi.example.test/simple"
    assert pip_env["PIP_TRUSTED_HOST"] == "pypi.example.test"
    assert "PIP_TARGET" not in pip_env
    assert npm_env["npm_config_registry"] == "https://npm.example.test/"
    assert "npm_config_registry" not in build_child_env(site="test-acp")


def test_app_setup_hook_runs_with_the_allowlisted_child_environment(
    monkeypatch, tmp_path
):
    home = tmp_path / "home"
    home.mkdir()
    _plant(monkeypatch, home)
    script = tmp_path / "record_hook.py"
    record = tmp_path / "hook-env.json"
    script.write_text(_recorder_script(record), encoding="utf-8")
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    _run_hook(command, cwd=tmp_path, timeout=15, env_name="onInstall")
    seen = json.loads(record.read_text(encoding="utf-8"))
    _assert_safe(seen)


def test_sidecar_install_child_gets_pip_settings_without_gateway_secrets(
    monkeypatch, tmp_path
):
    home = tmp_path / "home"
    home.mkdir()
    _plant(monkeypatch, home)
    record = tmp_path / "sidecar-env.json"
    installer = SidecarInstall("probe", venv=tmp_path / "venv")
    installer._run([sys.executable, "-c", _recorder_script(record)], timeout=15)
    seen = json.loads(record.read_text(encoding="utf-8"))
    _assert_safe(seen)
    assert seen["PIP_INDEX_URL"] == "https://pypi.example.test/simple"
    assert "PIP_TARGET" not in seen


def test_mcp_probe_child_gets_declared_values_without_gateway_secrets(
    monkeypatch, tmp_path
):
    home = tmp_path / "home"
    home.mkdir()
    _plant(monkeypatch, home)
    record = tmp_path / "mcp-env.json"
    script = tmp_path / "mcp_recorder.py"
    script.write_text(
        "import json, os, pathlib, sys\n"
        f"pathlib.Path({str(record)!r}).write_text(json.dumps(dict(os.environ)))\n"
        "for line in sys.stdin:\n"
        " request = json.loads(line)\n"
        " if request.get('id') == 1:\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'probe','version':'1'}}}), flush=True)\n"
        " elif request.get('id') == 2:\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':2,'result':{'tools':[]}}), flush=True)\n",
        encoding="utf-8",
    )
    server = McpServerInfo(
        name="probe-app:search",
        command=sys.executable,
        args=[str(script)],
        env={"MCP_PROBE_LABEL": "declared-value"},
    )
    asyncio.run(probe_server(server))
    seen = json.loads(record.read_text(encoding="utf-8"))
    _assert_safe(seen)
    assert seen["MCP_PROBE_LABEL"] == "declared-value"
