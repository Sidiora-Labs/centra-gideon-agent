"""Tests for PID tracking and orphan cleanup in session_pid.py."""

import os
import signal
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


@pytest.fixture()
def pid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect _pid_file_path to a temp file."""
    p = tmp_path / "agent_pids.txt"
    monkeypatch.setattr("gideon.engine.session_pid._pid_file_path", lambda: p)
    return p


@pytest.fixture()
def session_pid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect _session_pid_file_path to a temp file."""
    p = tmp_path / "session_pids.txt"
    monkeypatch.setattr("gideon.engine.session_pid._session_pid_file_path", lambda: p)
    return p


class TestTrackUntrack:
    def test_track_pid_creates_file(self, pid_file: Path) -> None:
        from gideon.engine.session_pid import _track_pid

        _track_pid(12345)
        assert "12345" in pid_file.read_text()

    def test_track_multiple(self, pid_file: Path) -> None:
        from gideon.engine.session_pid import _track_pid

        _track_pid(111)
        _track_pid(222)
        lines = pid_file.read_text().strip().splitlines()
        assert lines == ["111", "222"]

    def test_untrack_pid(self, pid_file: Path) -> None:
        from gideon.engine.session_pid import _track_pid, _untrack_pid

        _track_pid(111)
        _track_pid(222)
        _untrack_pid(111)
        lines = pid_file.read_text().strip().splitlines()
        assert lines == ["222"]

    def test_untrack_nonexistent(self, pid_file: Path) -> None:
        from gideon.engine.session_pid import _track_pid, _untrack_pid

        _track_pid(111)
        _untrack_pid(999)
        assert "111" in pid_file.read_text()

    def test_untrack_session_pid(self, session_pid_file: Path) -> None:
        from gideon.engine.session_pid import _track_session_pid, _untrack_session_pid

        _track_session_pid(111)
        _track_session_pid(222)
        _untrack_session_pid(111)
        gw = os.getpid()
        lines = session_pid_file.read_text().strip().splitlines()
        assert lines == [f"{gw}:222"]

    def test_untrack_session_pid_missing_file(self, session_pid_file: Path) -> None:
        from gideon.engine.session_pid import _untrack_session_pid

        _untrack_session_pid(999)
        assert not session_pid_file.exists()

    def test_untrack_session_pid_other_gateway_untouched(
        self, session_pid_file: Path
    ) -> None:
        """Untracking our PID must NOT remove other gateways' entries for same child PID."""
        from gideon.engine.session_pid import _track_session_pid, _untrack_session_pid

        _track_session_pid(111)
        with open(session_pid_file, "a", encoding="utf-8") as f:
            f.write("99999:111\n")
        _untrack_session_pid(111)
        lines = session_pid_file.read_text().strip().splitlines()
        assert lines == ["99999:111"]

    def test_track_child_pids_with_parent(self, pid_file: Path) -> None:
        from gideon.engine.session_pid import _track_child_pids

        _track_child_pids({100: None, 200: None, 300: None}, parent_pid=999)
        lines = pid_file.read_text().strip().splitlines()
        assert set(lines) == {"100:999", "200:999", "300:999"}

    def test_track_child_pids_dedup(self, pid_file: Path) -> None:
        """Duplicate child:parent entries should not be written."""
        from gideon.engine.session_pid import _track_child_pids

        _track_child_pids({100: None, 200: None}, parent_pid=999)
        _track_child_pids({100: None, 300: None}, parent_pid=999)
        lines = pid_file.read_text().strip().splitlines()
        assert sorted(lines) == ["100:999", "200:999", "300:999"]

    def test_untrack_child_pids(self, pid_file: Path) -> None:
        from gideon.engine.session_pid import _track_child_pids, _untrack_child_pids

        _track_child_pids({100: None, 200: None, 300: None}, parent_pid=999)
        _untrack_child_pids({100: None, 300: None})
        lines = pid_file.read_text().strip().splitlines()
        assert lines == ["200:999"]

    def test_untrack_child_pids_preserves_bare_pid(self, pid_file: Path) -> None:
        """Untracking child PIDs must not remove bare PID lines (gideon-cli parents)."""
        from gideon.engine.session_pid import (
            _track_child_pids,
            _track_pid,
            _untrack_child_pids,
        )

        _track_pid(100)
        _track_child_pids({100: None}, parent_pid=999)
        _untrack_child_pids({100: None})
        lines = pid_file.read_text().strip().splitlines()
        assert "100" in lines


class TestCleanupOrphanedMcpServers:
    def test_dead_child_pruned(self, pid_file: Path) -> None:
        """Dead child PIDs should be removed from the file silently."""
        from gideon.engine.session_pid import _cleanup_orphaned_mcp_servers

        pid_file.write_text("99999:1\n")
        _cleanup_orphaned_mcp_servers()
        assert "99999" not in pid_file.read_text()

    def test_alive_child_with_alive_parent_survives(self, pid_file: Path) -> None:
        """Child whose parent session is still alive should NOT be killed."""
        from gideon.engine.session_pid import _cleanup_orphaned_mcp_servers

        my_pid = os.getpid()
        child_pid = 77777
        pid_file.write_text(f"{child_pid}:{my_pid}\n")

        def fake_kill(pid: int, sig: int) -> None:
            if pid == child_pid and sig == 0:
                return
            if pid == my_pid and sig == 0:
                return
            raise ProcessLookupError

        with patch("os.kill", side_effect=fake_kill):
            killed = _cleanup_orphaned_mcp_servers()

        assert killed == 0
        assert str(child_pid) in pid_file.read_text()

    def test_alive_child_with_dead_parent_killed(self, pid_file: Path) -> None:
        """Child whose parent session died should be killed (PPid=1 confirms orphan)."""
        from gideon.engine.session_pid import _cleanup_orphaned_mcp_servers

        pid_file.write_text("77777:99999\n")

        def fake_kill(pid: int, sig: int) -> None:
            if pid == 77777 and sig == 0:
                return
            if pid == 99999 and sig == 0:
                raise ProcessLookupError

        orig_read = Path.read_text

        def patched_read(self_path: Path, *a: object, **kw: object) -> str:
            if "proc" in str(self_path) and "status" in str(self_path):
                return "Name:\tgideon-cli\nPPid:\t1\n"
            return orig_read(self_path, *a, **kw)  # type: ignore[arg-type]

        with (
            patch("os.kill", side_effect=fake_kill),
            patch.object(Path, "read_text", patched_read),
            patch("gideon.engine.session_pid.sys") as mock_sys,
        ):
            mock_sys.platform = "linux"
            killed = _cleanup_orphaned_mcp_servers()

        assert killed == 1

    def test_alive_child_with_dead_parent_killed_macos(self, pid_file: Path) -> None:
        """macOS: orphan detected via libproc ppid lookup."""
        from gideon.engine.session_pid import _cleanup_orphaned_mcp_servers

        pid_file.write_text("77777:99999\n")

        def fake_kill(pid: int, sig: int) -> None:
            if pid == 77777 and sig == 0:
                return
            if pid == 99999 and sig == 0:
                raise ProcessLookupError

        with (
            patch("os.kill", side_effect=fake_kill),
            patch("gideon.engine.session_pid.sys") as mock_sys,
            patch("gideon.engine.session_pid._get_ppid_libproc", return_value=1),
        ):
            mock_sys.platform = "darwin"
            killed = _cleanup_orphaned_mcp_servers()

        assert killed == 1

    def test_alive_child_with_dead_parent_pid_reused(self, pid_file: Path) -> None:
        """Child PID reused by unrelated process should NOT be killed."""
        from gideon.engine.session_pid import _cleanup_orphaned_mcp_servers

        pid_file.write_text("77777:99999\n")

        def fake_kill(pid: int, sig: int) -> None:
            if pid == 77777 and sig == 0:
                return
            if pid == 99999 and sig == 0:
                raise ProcessLookupError

        orig_read = Path.read_text

        def patched_read(self_path: Path, *a: object, **kw: object) -> str:
            if "proc" in str(self_path) and "status" in str(self_path):
                return "Name:\tvim\nPPid:\t5555\n"
            return orig_read(self_path, *a, **kw)  # type: ignore[arg-type]

        with (
            patch("os.kill", side_effect=fake_kill),
            patch.object(Path, "read_text", patched_read),
            patch("gideon.engine.session_pid.sys") as mock_sys,
        ):
            mock_sys.platform = "linux"
            killed = _cleanup_orphaned_mcp_servers()

        assert killed == 0
        assert "77777" not in pid_file.read_text()

    def test_alive_child_with_dead_parent_pid_reused_macos(
        self, pid_file: Path
    ) -> None:
        """macOS: reused PID detected via libproc returning unrelated PPid."""
        from gideon.engine.session_pid import _cleanup_orphaned_mcp_servers

        pid_file.write_text("77777:99999\n")

        def fake_kill(pid: int, sig: int) -> None:
            if pid == 77777 and sig == 0:
                return
            if pid == 99999 and sig == 0:
                raise ProcessLookupError

        with (
            patch("os.kill", side_effect=fake_kill),
            patch("gideon.engine.session_pid.sys") as mock_sys,
            patch("gideon.engine.session_pid._get_ppid_libproc", return_value=5555),
        ):
            mock_sys.platform = "darwin"
            killed = _cleanup_orphaned_mcp_servers()

        assert killed == 0
        assert "77777" not in pid_file.read_text()

    def test_bare_pid_dead_pruned(self, pid_file: Path) -> None:
        """Dead bare PIDs should be pruned from the file."""
        from gideon.engine.session_pid import _cleanup_orphaned_mcp_servers

        pid_file.write_text("99999\n")

        def fake_kill(pid: int, sig: int) -> None:
            if pid == 99999 and sig == 0:
                raise ProcessLookupError
            raise ProcessLookupError

        with patch("os.kill", side_effect=fake_kill):
            killed = _cleanup_orphaned_mcp_servers()
        assert killed == 0
        assert "99999" not in pid_file.read_text()

    def test_bare_pid_alive_kept(self, pid_file: Path) -> None:
        """Alive bare PIDs should be kept in the file."""
        from gideon.engine.session_pid import _cleanup_orphaned_mcp_servers

        pid_file.write_text("88888\n")

        def fake_kill(pid: int, sig: int) -> None:
            if pid == 88888 and sig == 0:
                return

        with patch("os.kill", side_effect=fake_kill):
            killed = _cleanup_orphaned_mcp_servers()
        assert killed == 0
        assert "88888" in pid_file.read_text()

    def test_empty_file(self, pid_file: Path) -> None:
        from gideon.engine.session_pid import _cleanup_orphaned_mcp_servers

        pid_file.write_text("")
        assert _cleanup_orphaned_mcp_servers() == 0

    def test_no_file(self, pid_file: Path) -> None:
        from gideon.engine.session_pid import _cleanup_orphaned_mcp_servers

        assert _cleanup_orphaned_mcp_servers() == 0


class TestCleanupOrphanedSessions:
    def test_preserves_non_agent_pids(self, session_pid_file: Path) -> None:
        """Bug fix: non-agent PIDs (MCP servers) must survive — not killed."""
        from gideon.engine.session_pid import cleanup_orphaned_sessions

        session_pid_file.write_text("99998\n99999\n")

        def fake_kill(pid: int, sig: int) -> None:
            if sig == 0:
                return

        with (
            patch(
                "gideon.engine.session_pid._is_managed_agent_process",
                side_effect=lambda p: p == 99998,
            ),
            patch(
                "gideon.engine.session_pid._cleanup_orphaned_mcp_servers",
                return_value=0,
            ),
            patch("os.kill", side_effect=fake_kill),
        ):
            cleanup_orphaned_sessions()

        content = session_pid_file.read_text()
        assert content == ""

    def test_agent_pids_killed(self, session_pid_file: Path) -> None:
        """ACP agent PIDs should be SIGKILL'd."""
        from gideon.engine.session_pid import cleanup_orphaned_sessions

        session_pid_file.write_text("99998\n")

        kills: list[tuple[int, int]] = []

        def fake_kill(pid: int, sig: int) -> None:
            kills.append((pid, sig))

        with (
            patch(
                "gideon.engine.session_pid._is_managed_agent_process", return_value=True
            ),
            patch("os.kill", side_effect=fake_kill),
            patch(
                "gideon.engine.session_pid._cleanup_orphaned_mcp_servers",
                return_value=0,
            ),
        ):
            cleanup_orphaned_sessions()

        assert (99998, signal.SIGKILL) in kills

    def test_malformed_pid_files_deleted(
        self, tmp_path: Path, session_pid_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malformed session_pid_*.txt files (e.g. MagicMock leak) should be deleted."""
        from gideon.engine.session_pid import cleanup_orphaned_sessions

        monkeypatch.setattr("gideon.engine.session_pid.config_dir", lambda: tmp_path)
        session_pid_file.write_text("")

        (tmp_path / "session_pid_99999.txt").write_text("sess-dead")
        (tmp_path / "session_pid_mock.get_pid().txt").write_text("sess-mock")

        with (
            patch(
                "gideon.engine.session_pid._cleanup_orphaned_mcp_servers",
                return_value=0,
            ),
            patch("os.kill", side_effect=ProcessLookupError),
        ):
            cleanup_orphaned_sessions()

        assert not (tmp_path / "session_pid_99999.txt").exists()
        assert not (tmp_path / "session_pid_mock.get_pid().txt").exists()

    def test_malformed_pid_file_unlink_oserror_continues(
        self, tmp_path: Path, session_pid_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError on malformed pid file unlink should not abort the cleanup loop."""
        from gideon.engine.session_pid import cleanup_orphaned_sessions

        monkeypatch.setattr("gideon.engine.session_pid.config_dir", lambda: tmp_path)
        session_pid_file.write_text("")

        (tmp_path / "session_pid_bad!name.txt").write_text("sess-bad")
        (tmp_path / "session_pid_99999.txt").write_text("sess-dead")

        original_unlink = Path.unlink

        def unlink_that_fails_on_bad(path_self, *a, **kw):
            if "bad!name" in path_self.name:
                raise OSError("permission denied")
            return original_unlink(path_self, *a, **kw)

        monkeypatch.setattr(Path, "unlink", unlink_that_fails_on_bad)

        with (
            patch(
                "gideon.engine.session_pid._cleanup_orphaned_mcp_servers",
                return_value=0,
            ),
            patch("os.kill", side_effect=ProcessLookupError),
        ):
            cleanup_orphaned_sessions()

        assert (tmp_path / "session_pid_bad!name.txt").exists()
        assert not (tmp_path / "session_pid_99999.txt").exists()


class TestResetStateUntracksParentPid:
    @pytest.mark.xfail(
        reason="pre-existing on main (v0.1.0 baseline) — #6", strict=False
    )
    def test_reset_state_untracks_parent_pid(self) -> None:
        """Verify _reset_state calls _untrack_pid with the saved PID."""
        from gideon.integrations.acp.client import AcpClient

        client = AcpClient.__new__(AcpClient)
        client._process = None
        client._pid = 54321
        client._session_id = None
        client._buffer = bytearray()
        client._cancelled = False
        client._resumed = False
        client._sandbox_handle = None
        client._child_pids = {}
        client._stderr_lines = deque(["some error"], maxlen=20)
        mock_task = Mock()
        mock_task.done.return_value = False
        client._stderr_task = mock_task

        with patch("gideon.engine.session._untrack_pid") as mock_untrack:
            client._reset_state()

        assert client._pid is None
        assert len(client._stderr_lines) == 0
        assert client._stderr_task is None
        mock_task.cancel.assert_called_once()
        mock_untrack.assert_called_once_with(54321)
