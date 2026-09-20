"""Tests for process tree tracking, recursive kill, and session cleanup."""

import os
import signal
import subprocess
import sys
from unittest.mock import patch

import pytest

from gideon.integrations.acp.client import (
    AcpProcess,
    _direct_children,
    _get_child_pids,
    _is_our_child,
    _kill_escaped_children,
)


class TestGetChildPidsVisitedSet:
    def test_cycle_terminates(self):
        """A→B→A cycle must not recurse infinitely."""
        call_count = 0

        def fake_direct(pid):
            nonlocal call_count
            call_count += 1
            return {1: [2], 2: [1]}.get(pid, [])

        with patch(
            "gideon.integrations.acp.transport._direct_children",
            side_effect=fake_direct,
        ):
            result = _get_child_pids(1)
        assert result == [2]
        assert call_count <= 3

    def test_self_loop(self):
        with patch(
            "gideon.integrations.acp.transport._direct_children", return_value=[42]
        ):
            assert _get_child_pids(42) == []

    def test_diamond_deduplicates(self):
        tree = {1: [2, 3], 2: [4], 3: [4]}
        with patch(
            "gideon.integrations.acp.transport._direct_children",
            side_effect=lambda p: tree.get(p, []),
        ):
            assert sorted(_get_child_pids(1)) == [2, 3, 4]

    def test_none_pid(self):
        assert _get_child_pids(None) == []

    def test_no_children(self):
        with patch(
            "gideon.integrations.acp.transport._direct_children", return_value=[]
        ):
            assert _get_child_pids(999) == []

    def test_deep_chain(self):
        tree = {1: [2], 2: [3], 3: [4], 4: [5]}
        with patch(
            "gideon.integrations.acp.transport._direct_children",
            side_effect=lambda p: tree.get(p, []),
        ):
            assert _get_child_pids(1) == [2, 3, 4, 5]


class TestKillEscapedChildren:
    def test_already_dead_pid(self):
        with patch("os.kill", side_effect=ProcessLookupError):
            _kill_escaped_children({999: 100})

    def test_kills_verified_child(self):
        def fake_kill(pid, sig):
            if sig == 0:
                return
            assert sig == signal.SIGKILL

        with (
            patch("os.kill", side_effect=fake_kill),
            patch("gideon.integrations.acp.transport._is_our_child", return_value=True),
        ):
            _kill_escaped_children({42: 100})

    def test_skips_recycled_pid(self):
        kills = []

        def fake_kill(pid, sig):
            kills.append((pid, sig))
            if sig == 0:
                return

        with (
            patch("os.kill", side_effect=fake_kill),
            patch(
                "gideon.integrations.acp.transport._is_our_child", return_value=False
            ),
        ):
            _kill_escaped_children({42: 100})
        assert all(sig == 0 for _, sig in kills)

    def test_kills_leaf_first(self):
        killed = []

        def fake_kill(pid, sig):
            if sig == signal.SIGKILL:
                killed.append(pid)

        with (
            patch("os.kill", side_effect=fake_kill),
            patch("gideon.integrations.acp.transport._is_our_child", return_value=True),
        ):
            _kill_escaped_children({10: 1, 20: 2, 30: 3})
        assert killed == [30, 20, 10]


class TestIsOurChild:
    @pytest.fixture(autouse=True)
    def _force_linux(self):
        with patch("gideon.integrations.acp.transport.sys") as mock_sys:
            mock_sys.platform = "linux"
            yield

    def test_rejects_missing_proc(self):
        assert _is_our_child(-1, expected_start=1) is False

    def test_rejects_unknown_binary(self):
        with patch("gideon.integrations.acp.transport.Path") as mock_path_cls:
            inst = mock_path_cls.return_value
            inst.exists.return_value = True
            inst.read_bytes.return_value = b"postgres\x00--flag"
            assert _is_our_child(999, expected_start=1) is False

    def test_rejects_start_time_mismatch(self):
        with (
            patch("gideon.integrations.acp.transport.Path") as mock_path_cls,
            patch(
                "gideon.integrations.acp.transport._get_start_time", return_value=200
            ),
        ):
            inst = mock_path_cls.return_value
            inst.exists.return_value = True
            inst.read_bytes.return_value = b"claude\x00acp"
            assert _is_our_child(999, expected_start=100) is False

    def test_accepts_matching_agent(self):
        with (
            patch("gideon.integrations.acp.transport.Path") as mock_path_cls,
            patch(
                "gideon.integrations.acp.transport._get_start_time", return_value=100
            ),
        ):
            inst = mock_path_cls.return_value
            inst.exists.return_value = True
            inst.read_bytes.return_value = b"claude\x00acp"
            assert _is_our_child(999, expected_start=100) is True

    def test_accepts_mcp_in_name(self):
        with (
            patch("gideon.integrations.acp.transport.Path") as mock_path_cls,
            patch("gideon.integrations.acp.transport._get_start_time", return_value=50),
        ):
            inst = mock_path_cls.return_value
            inst.exists.return_value = True
            inst.read_bytes.return_value = b"my-mcp-server\x00serve"
            assert _is_our_child(999, expected_start=50) is True

    def test_none_start_time_denied(self):
        with patch("gideon.integrations.acp.transport.Path") as mock_path_cls:
            inst = mock_path_cls.return_value
            inst.exists.return_value = True
            inst.read_bytes.return_value = b"gideon-cli\x00acp"
            assert _is_our_child(999, expected_start=None) is False


class TestDirectChildren:
    def test_proc_children_parsed(self):
        with subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        ) as child:
            try:
                assert child.pid in _direct_children(os.getpid())
            finally:
                child.terminate()
                child.wait(timeout=5)


class TestSnapshotProcessTree:

    @pytest.mark.asyncio
    async def test_tracks_all_descendants(self, tmp_path):
        proc = AcpProcess.__new__(AcpProcess)
        proc._pid = 100
        proc._child_pids = {}

        with (
            patch(
                "gideon.integrations.acp.transport._get_child_pids",
                return_value=[200, 300, 400],
            ),
            patch(
                "gideon.integrations.acp.transport._get_start_time",
                side_effect=lambda p: p * 10,
            ),
            patch("gideon.engine.session_pid.config_dir", return_value=tmp_path),
        ):
            await proc.snapshot_process_tree()

        assert proc._child_pids == {200: 2000, 300: 3000, 400: 4000}
        content = (tmp_path / "agent_pids.txt").read_text()
        lines = {ln.strip() for ln in content.splitlines() if ln.strip()}
        assert lines == {"200:100", "300:100", "400:100"}

    @pytest.mark.asyncio
    async def test_no_descendants_no_tracking(self):
        proc = AcpProcess.__new__(AcpProcess)
        proc._pid = 100
        proc._child_pids = {}

        with patch(
            "gideon.integrations.acp.transport._get_child_pids", return_value=[]
        ):
            await proc.snapshot_process_tree()

        assert proc._child_pids == {}

    @pytest.mark.asyncio
    async def test_merges_early_and_late_snapshots(self, tmp_path):
        """Early snapshot from spawn() + late snapshot from snapshot_process_tree() merge."""
        proc = AcpProcess.__new__(AcpProcess)
        proc._pid = 100
        proc._child_pids = {200: 2000}

        with (
            patch(
                "gideon.integrations.acp.transport._get_child_pids",
                return_value=[200, 300],
            ),
            patch(
                "gideon.integrations.acp.transport._get_start_time",
                side_effect=lambda p: p * 10,
            ),
            patch("gideon.engine.session_pid.config_dir", return_value=tmp_path),
        ):
            await proc.snapshot_process_tree()

        assert proc._child_pids == {200: 2000, 300: 3000}


class TestPidTracking:
    def test_track_and_untrack(self, tmp_path):
        """_track_pid appends, _untrack_pid removes."""
        from gideon.engine.session_pid import _track_pid, _untrack_pid

        pid_file = tmp_path / "pids.txt"
        with patch("gideon.engine.session_pid._pid_file_path", return_value=pid_file):
            _track_pid(100)
            _track_pid(200)
            _track_pid(300)
            assert "100" in pid_file.read_text()
            assert "200" in pid_file.read_text()

            _untrack_pid(200)
            content = pid_file.read_text()
            assert "200" not in content
            assert "100" in content
            assert "300" in content
