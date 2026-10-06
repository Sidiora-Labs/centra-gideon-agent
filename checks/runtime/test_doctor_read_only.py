"""Doctor reads actual home config; explicit repair preserves owner permissions."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_probe(home, code):
    env = dict(
        os.environ,
        GIDEON_HOME=str(home),
        HOME=str(home),
        PYTHONPATH=str(ROOT / "runtime"),
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def config(home, text):
    path = home / "agents" / "gideon.json"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    return path


def test_actual_cli_and_registered_probe_do_not_change_owner_file(tmp_path):
    path = config(
        tmp_path,
        json.dumps(
            {"tools": [], "allowedTools": [], "mcpServers": {}, "owner": "keep"}
        ),
    )
    before = path.read_bytes()
    output = run_probe(
        tmp_path,
        """import asyncio
from gideon.interfaces.cli.doctor import _doctor_core_server
from gideon.operations.resilience.core_server import probe_core_server
assert _doctor_core_server()
result=asyncio.run(probe_core_server(None))
assert not result.ok
print(result.detail)
""",
    )
    assert "no entry" in output
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_diagnostic_missing_home_creates_nothing(tmp_path):
    home = tmp_path / "absent"
    run_probe(
        home,
        "from gideon.interfaces.cli.doctor import _doctor_core_server; _doctor_core_server()",
    )
    assert not home.exists()


def test_actual_explicit_repair_preserves_permissions_and_other_servers(tmp_path):
    original = {
        "tools": ["@other"],
        "allowedTools": [],
        "mcpServers": {
            "other": {"command": "keep"},
            "gideon-core": {
                "command": "/missing",
                "args": ["stale"],
                "env": {"KEEP": "value"},
            },
        },
        "owner": {"keep": True},
    }
    path = config(tmp_path, json.dumps(original))
    output = run_probe(
        tmp_path,
        """from gideon.operations.resilience.core_server import core_server_command
from gideon.operations.resilience.fixes import get_fix
command=core_server_command()
assert command, "actual Gideon executable unavailable"
fix=get_fix("tools.restore-core-server")
print(fix.dry_preview())
print(fix.apply())
""",
    )
    after = json.loads(path.read_text())
    assert (
        after["tools"] == original["tools"]
        and after["allowedTools"] == original["allowedTools"]
    )
    assert (
        after["owner"] == original["owner"]
        and after["mcpServers"]["other"] == original["mcpServers"]["other"]
    )
    assert after["mcpServers"]["gideon-core"]["env"] == {"KEEP": "value"}
    assert after["mcpServers"]["gideon-core"]["args"] == ["mcp-core"]
    assert "Repaired" in output


def test_malformed_file_is_reported_and_repair_refuses(tmp_path):
    path = config(tmp_path, "{malformed")
    output = run_probe(
        tmp_path,
        """from gideon.interfaces.cli.doctor import _doctor_core_server
from gideon.operations.resilience.fixes import get_fix
assert _doctor_core_server()
try: get_fix("tools.restore-core-server").apply()
except RuntimeError as exc: print(exc)
else: raise AssertionError("malformed config overwritten")
""",
    )
    assert "nothing was written" in output
    assert path.read_text() == "{malformed"
