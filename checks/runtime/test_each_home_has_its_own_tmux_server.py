from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import pytest


def test_tmux_socket_identity_and_tmpdir_are_per_home(monkeypatch):
    from gideon.engine import tmux_substrate

    with tempfile.TemporaryDirectory(prefix="g1-", dir="/tmp") as first_dir:
        with tempfile.TemporaryDirectory(prefix="g2-", dir="/tmp") as second_dir:
            first, second = Path(first_dir), Path(second_dir)
            monkeypatch.setenv("GIDEON_HOME", str(first))
            first_socket = tmux_substrate.socket_name()
            first_env = tmux_substrate.command_env()
            monkeypatch.setenv("GIDEON_HOME", str(second))
            second_socket = tmux_substrate.socket_name()
            second_env = tmux_substrate.command_env()
            assert first_socket != second_socket
            assert first_env["TMUX_TMPDIR"] == str(first / "tmux")
            assert second_env["TMUX_TMPDIR"] == str(second / "tmux")
            assert tmux_substrate._argv("list-sessions")[2] == second_socket
            assert (
                tmux_substrate.terminal_attach_argv("terminal-1", ["sh", "-l"])[2]
                == second_socket
            )


def test_long_home_uses_bounded_socket_path_with_distinct_identity(
    tmp_path, monkeypatch
):
    from gideon.engine import tmux_substrate

    first = tmp_path / ("a" * 60) / ("b" * 60)
    second = tmp_path / ("a" * 60) / ("c" * 60)
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    monkeypatch.setenv("GIDEON_HOME", str(first))
    first_name = tmux_substrate.socket_name()
    first_tmp = tmux_substrate.command_env()["TMUX_TMPDIR"]
    monkeypatch.setenv("GIDEON_HOME", str(second))
    second_name = tmux_substrate.socket_name()
    second_tmp = tmux_substrate.command_env()["TMUX_TMPDIR"]
    assert first_name != second_name
    assert first_tmp == second_tmp == "/tmp"
    assert len(os.fsencode(f"/tmp/tmux-{os.getuid()}/{first_name}")) <= 103


@pytest.mark.skipif(shutil.which("tmux") is None, reason="requires tmux")
def test_sessions_from_two_homes_are_invisible_to_each_other(tmp_path, monkeypatch):
    from gideon.engine import tmux_substrate

    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    name = tmux_substrate.durable_session_name("project", "run", "private")
    monkeypatch.setenv("GIDEON_HOME", str(first))
    try:
        assert asyncio.run(
            tmux_substrate.new_session(
                name, workspace=str(first), command=["sleep", "30"]
            )
        )
        assert tmux_substrate.has_session_sync(name)
        monkeypatch.setenv("GIDEON_HOME", str(second))
        assert not tmux_substrate.has_session_sync(name)
        assert asyncio.run(tmux_substrate.list_sessions()) == []
    finally:
        monkeypatch.setenv("GIDEON_HOME", str(first))
        tmux_substrate.TmuxCommand(("kill-server",)).status_sync()
