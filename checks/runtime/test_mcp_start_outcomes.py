"""Startup outcomes and retained first starts use real owned processes."""

import asyncio
import os
import sys

import pytest

from gideon.integrations import mcp_client, mcp_discovery, mcp_stdio
from test_mcp_abandoned_queue import configured, until, SERVER


@pytest.mark.asyncio
async def test_failed_start_count_is_shared_and_retry_clears_exit_detail(tmp_path):
    program = "import sys\nsys.stderr.write('x'*12000+'\\nModuleNotFoundError: startup dependency\\n')\nsys.exit(7)\n"
    spec, log = configured(tmp_path, program=program, name="broken")
    for _ in range(3):
        conn = mcp_client.McpServerConn("broken", spec)
        assert not await conn.ensure_started()
        assert "exited with code 7" in conn.error
        assert "ModuleNotFoundError" in conn.error
        await conn.shutdown()
    info = next(row for row in mcp_discovery.list_servers() if row.name == "broken")
    assert info.status == "stopped"
    assert len(info.detail.encode()) <= 8192
    stopped = mcp_client.McpServerConn("broken", spec)
    assert not await stopped.ensure_started()
    assert stopped._task is None
    mcp_discovery.forget_probe("broken")
    (tmp_path / "server.py").write_text(SERVER)
    conn = mcp_client.McpServerConn("broken", spec)
    try:
        assert await conn.ensure_started()
        info = next(row for row in mcp_discovery.list_servers() if row.name == "broken")
        assert info.status == "ok" and info.error == "" and info.detail == ""
    finally:
        await conn.shutdown()


@pytest.mark.asyncio
async def test_silent_first_start_finishes_without_failure_and_cleanup_reaps(tmp_path):
    program = "import os,sys,time\nfrom pathlib import Path\nPath(sys.argv[1]).with_suffix('.pid').write_text(str(os.getpid()))\ntime.sleep(30)\n"
    spec, log = configured(tmp_path, program=program, name="installing")
    conn = mcp_client.McpServerConn("installing", spec, connect_timeout=.4)
    try:
        assert not await conn.ensure_started()
        assert conn._failure.pending and not conn._failure.counts
        assert "still starting" in conn.error
        assert mcp_discovery._probe_cache["installing"].failures == 0
        assert mcp_stdio._finishing
        pid = int(log.with_suffix(".pid").read_text())
        os.kill(pid, 0)
        other = mcp_client.McpServerConn("installing", spec, connect_timeout=.03)
        assert not await other.ensure_started()
        assert "earlier start" in other.error
        await other.shutdown()
        await mcp_stdio.stop_finishing(lambda name: name == "installing")
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert not any(entry.server == "installing" for entry in mcp_stdio._finishing.values())
    finally:
        await conn.shutdown()
        await mcp_stdio.stop_finishing(lambda name: name == "installing")
