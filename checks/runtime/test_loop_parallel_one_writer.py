"""A provider child must be drained before parallel work can take ownership."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from types import SimpleNamespace

import pytest

from gideon.automation.loop import manager
from gideon.automation.triggers.nudge import AutoNudgeService
from gideon.engine import session_pid


@pytest.mark.asyncio
async def test_stage_and_task_workers_never_write_same_tree_concurrently(
    tmp_path, monkeypatch
):
    """An active stage child blocks task handoff; only confirmed death unlocks it."""
    monkeypatch.setattr(session_pid, "config_dir", lambda: tmp_path)
    service = AutoNudgeService(base_dir=tmp_path)
    stage_key = "loop-one-writer-stage"
    task_key = "loop-one-writer-stage-task-1"
    nudge = await service.add(
        session_name=stage_key,
        message="stage",
        idle_secs=60,
    )
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    note = tmp_path / f"session_pid_{child.pid}.txt"
    note.write_text(stage_key, encoding="utf-8")
    state = SimpleNamespace(_sessions={})
    try:
        assert manager.recorded_session_process(stage_key) is True
        assert not await manager.worker_is_drained(
            state, service, stage_key, stop_nudge=True
        )
        assert service.get_by_session(stage_key).active is False
        assert service.get_by_session(task_key) is None

        child.terminate()
        child.wait(timeout=5)
        assert await manager.worker_is_drained(
            state, service, stage_key, stop_nudge=False
        )
        assert manager.recorded_session_process("loop-with-no-note") is None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        await service.remove(nudge.id)
