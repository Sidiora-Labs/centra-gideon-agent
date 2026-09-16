import asyncio
import sys

import pytest

from gideon.core.cancellation import CancelScope, kill_timed_out, terminate_and_reap
from gideon.core.record_ids import UnsafeRecordId, record_path
from gideon.core.textfmt import strip_thinking_tags


@pytest.mark.asyncio
async def test_concurrent_reapers_retire_each_real_child_once():
    scope = CancelScope()
    scope.begin_turn()
    children = []
    try:
        for _ in range(2):
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
                start_new_session=True,
            )
            children.append(child)
            scope.register_child(child)
        results = await asyncio.gather(scope.reap_children(), scope.reap_children())
        assert sorted(results) == [0, 2]
        assert scope.report.children_reaped == 2
        assert scope.report.children_escaped == 0
        assert all(child.returncode is not None for child in children)
        assert scope.child_count == 0
    finally:
        for child in children:
            if child.returncode is None:
                await kill_timed_out(child)


@pytest.mark.asyncio
async def test_reaper_escalates_after_child_confirms_sigterm_is_ignored():
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready',flush=True); time.sleep(60)",
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        assert await asyncio.wait_for(child.stdout.readline(), 3) == b"ready\n"
        assert await terminate_and_reap(child, grace=0.1)
        assert child.returncode is not None
    finally:
        if child.returncode is None:
            await kill_timed_out(child)


def test_record_symlink_cannot_escape_the_store(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("preserve", encoding="utf-8")
    (store / "record.json").symlink_to(outside)
    with pytest.raises(UnsafeRecordId):
        record_path(store, "record")
    assert outside.read_text(encoding="utf-8") == "preserve"


def test_thinking_blocks_preserve_unrelated_inline_tags_and_spacing():
    visible, thoughts = strip_thinking_tags(
        " first <thinking> <b>one</b> </thinking> next <antml:thinking>two</antml:thinking> last ",
        strip_whitespace=False,
    )
    assert visible == " first  next  last "
    assert thoughts == "<b>one</b>\n\ntwo"
