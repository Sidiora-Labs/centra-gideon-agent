"""Real lifecycle refusals and Linux process facts without lookup utilities."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from gideon.engine import gateway_base
from gideon.interfaces.cli import server
from gideon.operations import container_host
from test_container_host import assert_install_commands


def test_containerized_gateway_stop_reports_host_action_without_lsof_or_ps(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    assert_install_commands(monkeypatch)
    for command, expected in (
        ("/opt/venv/bin/python /opt/venv/bin/gideon gateway", True),
        ("/usr/bin/python3 -m gideon gateway --port 10000", True),
        ("/home/u/.local/bin/gideon gateway --no-open", True),
        ("/Applications/Gideon.app/Contents/Resources/backend-dist/gideon-backend gateway --port auto --json-ready --no-open", True),
        ("/opt/venv/bin/python /opt/venv/bin/gideon token", False),
        ("vim /tmp/gideon-notes.txt", False),
        ("sh -c sleep 60 gateway", False),
        ("nginx: worker process", False),
        ("", False),
        ("python -c 'import time; time.sleep(120)' gideon gateway", False),
    ):
        assert server._runs_the_gateway(command) is expected
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        monkeypatch.setenv("PATH", "")
        facts = gateway_base.process_facts(child.pid)
        assert facts is not None
        assert facts.argv[:2] == (sys.executable, "-c")
        assert facts.identity
        gateway_base.publish(10000, pid=child.pid)
        recorded = gateway_base.BoundGateway.read(gateway_base._runtime_path())
        assert recorded.identity == facts.identity

        def refuse_program(*args, **kwargs):
            raise AssertionError(f"Lifecycle attempted an external program: {args}")

        with monkeypatch.context() as guard:
            guard.setattr(subprocess, "run", refuse_program)
            guard.setattr(subprocess, "check_output", refuse_program)
            guard.setattr(subprocess, "Popen", refuse_program)
            monkeypatch.setenv("GIDEON_INSTALL_KIND", "container")
            for started_by, base in (
                ("docker-run", "docker"),
                ("compose", "docker compose -f infrastructure/compose/compose.yaml"),
            ):
                monkeypatch.setenv(container_host.STARTED_BY_ENV, started_by)
                for action, verb in ((server._stop, "stopped"), (server._restart, "restarted")):
                    with pytest.raises(SystemExit) as failure:
                        action(10000)
                    assert failure.value.code == 1
                    error = capsys.readouterr().err
                    assert f"Nothing was {verb}." in error
                    assert base in error
                    assert (container_host.stop_command() if verb == "stopped" else container_host.restart_command()) in error
                    assert child.poll() is None
            monkeypatch.delenv("GIDEON_INSTALL_KIND")
            for action in (server._stop, server._restart):
                with pytest.raises(SystemExit) as failure:
                    action(10000)
                assert failure.value.code == 1
                assert "verify" in capsys.readouterr().err
                assert child.poll() is None
            gateway_base._runtime_path().unlink()
            monkeypatch.setenv("GIDEON_INSTALL_KIND", "container")
            with pytest.raises(SystemExit):
                server._restart(10000)
            assert "Nothing was restarted." in capsys.readouterr().err
            assert child.poll() is None
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
