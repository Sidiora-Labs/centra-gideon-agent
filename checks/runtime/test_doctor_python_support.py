"""Interpreter diagnosis reads actual installed distribution metadata."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gideon.core.python_support import python_support, rebuild_command

_REPO = Path(__file__).resolve().parents[2]


def _probe(tmp_path, requires, version, *, doctor=False):
    info = tmp_path / "gideon_agent_harness-0.1.0.dist-info"
    info.mkdir()
    info.joinpath("METADATA").write_text(
        "Metadata-Version: 2.1\nName: gideon-agent-harness\nVersion: 0.1.0\n"
        + (f"Requires-Python: {requires}\n" if requires is not None else "")
    )
    if doctor:
        code = (
            "from gideon.interfaces.cli.doctor import _interpreter_row; "
            f"issues=[]; _interpreter_row('python', 'runtime', {version!r}, issues); print(issues)"
        )
    else:
        code = (
            "import json; from dataclasses import asdict; "
            "from gideon.core.python_support import python_support; "
            f"print(json.dumps(asdict(python_support({version!r}))))"
        )
    env = dict(
        os.environ, PYTHONPATH=os.pathsep.join([str(tmp_path), str(_REPO / "runtime")])
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.mark.parametrize(
    "version, supported", [("3.13.1", True), ("3.11.9", False), ("3.14.0rc1", False)]
)
def test_python_range_comparison_uses_installed_metadata(tmp_path, version, supported):
    result = json.loads(_probe(tmp_path, ">=3.12,<3.14", version))
    assert result["supported"] is supported
    assert result["requires"] == ">=3.12,<3.14"


@pytest.mark.parametrize(
    "requires, version",
    [(None, "3.13.1"), ("broken", "3.13.1"), (">=3.12", "unreadable")],
)
def test_unknown_metadata_or_interpreter_is_not_certified(tmp_path, requires, version):
    result = json.loads(_probe(tmp_path, requires, version))
    assert result["supported"] is None
    assert result["unknown"]


def test_absent_distribution_is_unknown():
    assert (
        python_support("3.13.1", distribution="missing-runtime-support-test").supported
        is None
    )


def test_doctor_reports_unsupported_version_and_actionable_fix(tmp_path):
    output = _probe(tmp_path, ">=3.12,<3.14", "3.14.0", doctor=True)
    assert "❌" in output
    assert "recreate this environment" in output
    assert "python version" in output


def test_doctor_unknown_range_has_no_success_mark(tmp_path):
    output = _probe(tmp_path, None, "3.13.1", doctor=True)
    assert "⏹" in output
    assert "✅" not in output


def test_only_a_tool_receipt_has_an_in_place_rebuild_command(tmp_path):
    assert rebuild_command(">=3.12,<3.14", environment=tmp_path) == ""
    (tmp_path / "uv-receipt.toml").write_text('tool = "gideon-agent-harness"\n')
    command = rebuild_command(">=3.12,<3.14", environment=tmp_path)
    assert "--python '>=3.12,<3.14'" in command
    assert command.endswith("gideon-agent-harness")
