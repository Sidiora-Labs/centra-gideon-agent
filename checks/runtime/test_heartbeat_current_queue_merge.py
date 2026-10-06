import asyncio
import multiprocessing
import time

import pytest

from gideon.core.atomic_write import atomic_write_bytes
from gideon.engine import heartbeat as hb
from gideon.engine.heartbeat_store import queue_lock


def grant(path):
    for row in hb.heartbeat_task_rows(path.read_bytes().decode()):
        assert hb.allow_heartbeat_task(
            row["id"], seen=row["revision"], principal="owner"
        )


@pytest.mark.asyncio
async def test_pass_preserves_concurrent_edits_and_crlf(tmp_path, monkeypatch):
    path = tmp_path / "HEARTBEAT.md"
    monkeypatch.setattr(hb, "heartbeat_path", lambda: path)
    path.write_bytes(b"# Mine\r\n- finish\r\n- edit me\r\n<!-- note -->\r\n")
    grant(path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def run(text, destination):
        started.set()
        await release.wait()
        return "done"

    task = asyncio.create_task(hb.HeartbeatService(on_task=run).run_tasks())
    await started.wait()
    with queue_lock(path):
        atomic_write_bytes(
            path,
            b"# New header\r\n- finish\r\n* owner edited\r\n<!-- changed note -->\r\n- appended\r\n",
        )
    release.set()
    result = await task
    assert result["completed"] == 2
    assert (
        path.read_bytes()
        == b"# New header\r\n* owner edited\r\n<!-- changed note -->\r\n- appended\r\n"
    )
    assert all(not row["allowed"] for row in hb.heartbeat_task_rows())


@pytest.mark.asyncio
async def test_duplicates_use_occurrence_and_destination(tmp_path, monkeypatch):
    path = tmp_path / "HEARTBEAT.md"
    monkeypatch.setattr(hb, "heartbeat_path", lambda: path)
    path.write_bytes(b"- same\n* same\n- same <!-- deliver:other -->\n")
    grant(path)
    count = 0

    async def run(text, destination):
        nonlocal count
        count += 1
        return "done" if count == 2 else "HEARTBEAT_KEEP"

    result = await hb.HeartbeatService(on_task=run).run_tasks()
    assert result["completed"] == 1
    assert path.read_bytes() == b"- same\n- same <!-- deliver:other -->\n"


@pytest.mark.asyncio
async def test_no_completion_or_revoke_failure_never_writes(tmp_path, monkeypatch):
    path = tmp_path / "HEARTBEAT.md"
    monkeypatch.setattr(hb, "heartbeat_path", lambda: path)
    path.write_bytes(b"# header\r\n* keep\r\n")
    grant(path)
    initial = path.stat().st_mtime_ns

    async def keep(*args):
        return "HEARTBEAT_KEEP"

    await hb.HeartbeatService(on_task=keep).run_tasks()
    assert path.stat().st_mtime_ns == initial

    def refuse(*args):
        raise OSError("unavailable grant store")

    monkeypatch.setattr(hb._HEARTBEAT_GRANTS, "revoke", refuse)

    async def done(*args):
        return "done"

    assert (await hb.HeartbeatService(on_task=done).run_tasks())["completed"] == 0
    assert path.stat().st_mtime_ns == initial
    assert path.read_bytes() == b"# header\r\n* keep\r\n"


def child_lock(path, entered):
    with queue_lock(path):
        entered.set()


def test_queue_lock_excludes_another_process(tmp_path, monkeypatch):
    path = tmp_path / "HEARTBEAT.md"
    monkeypatch.setattr(hb, "heartbeat_path", lambda: path)
    ctx = multiprocessing.get_context("fork")
    entered = ctx.Event()
    with queue_lock(path):
        process = ctx.Process(target=child_lock, args=(path, entered))
        process.start()
        assert not entered.wait(0.1)
    assert entered.wait(2)
    process.join(2)
    assert process.exitcode == 0


@pytest.mark.asyncio
async def test_failure_cancel_and_waiting_tasks_keep_original_bytes(
    tmp_path, monkeypatch
):
    path = tmp_path / "HEARTBEAT.md"
    monkeypatch.setattr(hb, "heartbeat_path", lambda: path)
    original = b"# header\r\n- failed\r\n- cancelled\r\n- waiting\r\n"
    path.write_bytes(original)
    rows = hb.heartbeat_task_rows()
    for row in rows[:2]:
        assert hb.allow_heartbeat_task(
            row["id"], seen=row["revision"], principal="owner"
        )

    async def run(text, destination):
        if text == "failed":
            raise ValueError("failure")
        raise asyncio.CancelledError()

    result = await hb.HeartbeatService(on_task=run).run_tasks()
    assert result["processed"] == 2 and result["completed"] == 0
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_completion_revokes_authority_before_atomic_removal(
    tmp_path, monkeypatch
):
    from gideon.core.atomic_write import (
        register_post_write_hook,
        unregister_post_write_hook,
    )

    path = tmp_path / "HEARTBEAT.md"
    monkeypatch.setattr(hb, "heartbeat_path", lambda: path)
    path.write_text("- done\n")
    grant(path)
    row = hb.heartbeat_task_rows()[0]
    content = hb._task_content(next(hb._task_lines("- done\n")))
    observed = []

    def after_write(destination):
        if destination == path:
            observed.append(hb._HEARTBEAT_GRANTS.holds(row["id"], content))

    register_post_write_hook(after_write)

    async def run(*args):
        return "done"

    try:
        await hb.HeartbeatService(on_task=run).run_tasks()
    finally:
        unregister_post_write_hook(after_write)
    assert observed == [False] and path.read_bytes() == b""


def hold_in_child(path, entered, release):
    with queue_lock(path):
        entered.set()
        assert release.wait(3)


@pytest.mark.asyncio
async def test_native_write_and_edit_wait_for_queue_lock(tmp_path, monkeypatch):
    from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider

    path = tmp_path / "HEARTBEAT.md"
    monkeypatch.setattr(hb, "heartbeat_path", lambda: path)
    path.write_text("- original\n")
    provider = NativeBuiltinToolProvider(tmp_path)
    ctx = multiprocessing.get_context("fork")
    for operation, args in (
        (provider._t_write_file, {"path": "HEARTBEAT.md", "content": "- changed\n"}),
        (
            provider._t_edit_file,
            {"path": "HEARTBEAT.md", "old_str": "changed", "new_str": "edited"},
        ),
    ):
        entered, release = ctx.Event(), ctx.Event()
        process = ctx.Process(target=hold_in_child, args=(path, entered, release))
        process.start()
        assert entered.wait(2)
        try:
            task = asyncio.create_task(operation(args))
            await asyncio.sleep(0.1)
            assert not task.done()
            release.set()
            result = await asyncio.wait_for(task, 2)
            assert result.success
        finally:
            release.set()
            process.join(2)
        assert process.exitcode == 0
    assert path.read_text() == "- edited\n"
