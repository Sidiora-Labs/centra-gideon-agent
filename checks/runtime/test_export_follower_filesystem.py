"""Configuration export follower and execution locks against real files and threads."""

import json
import os
import threading
import time

import pytest

from gideon.core.atomic_write import atomic_json_write, post_write_hooks
from gideon.core.concurrency import lock_path, single_flight
from gideon.operations.durability.export_follow import (
    ExportFollower,
    install,
    uninstall,
)
from gideon.operations.durability.home_paths import LinkInTheWay
from gideon.operations.durability.shards import export_shards, import_shards


def wait_until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except ValueError:
            # A real export may replace a shard before its manifest; wait for that generation.
            pass
        time.sleep(0.02)
    assert predicate()


def seed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    atomic_json_write(
        home / "mcp.json", {"servers": [{"name": "one", "args": ["old-value"]}]}
    )
    export_shards(home, home / "shards")
    return home


def test_real_atomic_write_reexports_current_configuration(tmp_path, monkeypatch):
    home = seed(tmp_path, monkeypatch)
    follower = install(home=home)
    try:
        atomic_json_write(home / "mcp.json", {"servers": []})
        wait_until(
            lambda: import_shards(home / "shards").rows.get("mcp", [{}])[0].get("data")
            == {"servers": []}
        )
        assert "old-value" not in (home / "shards" / "mcp" / "value.jsonl").read_text()
    finally:
        uninstall(follower)
    assert follower.notify not in post_write_hooks()


def test_deleted_store_physically_removed_from_recognized_export(tmp_path, monkeypatch):
    home = seed(tmp_path, monkeypatch)
    follower = ExportFollower(home)
    (home / "mcp.json").unlink()
    assert follower.notify(home / "mcp.json")
    assert not (home / "shards" / "mcp").exists()
    assert "mcp" not in import_shards(home / "shards").rows
    follower.stop()


def test_stop_cancels_retry_real_worker_and_stale_cleanup_keeps_new_owner(
    tmp_path, monkeypatch
):
    home = seed(tmp_path, monkeypatch)
    first = install(home=home)
    wait_until(lambda: not any(timer.is_alive() for timer in first._timers))
    with single_flight("durability:export") as acquired:
        assert acquired
        first.notify(home / "mcp.json")
        assert first._timers
        first.stop()
    secondhome = tmp_path / "second"
    secondhome.mkdir()
    export_shards(secondhome, secondhome / "shards")
    second = install(home=secondhome)
    try:
        uninstall(first)
        assert second.notify in post_write_hooks()
        assert not any(timer.is_alive() for timer in first._timers)
        assert first.notify(home / "mcp.json") is False
    finally:
        uninstall(second)


def test_timer_start_and_stop_are_ordered_real_threads(tmp_path):
    follower = ExportFollower(tmp_path)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def work():
        started.set()
        release.wait(2)
        finished.set()

    follower._on_a_timer(0, work)
    assert started.wait(1)
    stopped = threading.Event()
    waiter = threading.Thread(target=lambda: (follower.stop(), stopped.set()))
    waiter.start()
    assert not stopped.wait(0.05)
    release.set()
    waiter.join(1)
    assert finished.is_set() and stopped.is_set()
    assert not any(timer.is_alive() for timer in follower._timers)


def test_lock_does_not_truncate_metadata_or_follow_link(tmp_path, monkeypatch):
    home = seed(tmp_path, monkeypatch)
    path = lock_path("example")
    path.write_bytes(b"owner metadata")
    inode = path.stat().st_ino
    with single_flight("example") as acquired:
        assert acquired
    assert path.read_bytes() == b"owner metadata" and path.stat().st_ino == inode
    path.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b"private original")
    outside.chmod(0o644)
    path.symlink_to(outside)
    with pytest.raises(LinkInTheWay):
        with single_flight("example"):
            pass
    assert (
        outside.read_bytes() == b"private original"
        and outside.stat().st_mode & 0o777 == 0o644
    )
    path.unlink()
    os.link(outside, path)
    with pytest.raises(LinkInTheWay):
        with single_flight("example"):
            pass
    assert (
        outside.read_bytes() == b"private original"
        and outside.stat().st_mode & 0o777 == 0o644
    )


def test_linked_export_directory_not_removed(tmp_path, monkeypatch):
    home = seed(tmp_path, monkeypatch)
    shard = home / "shards" / "mcp"
    import shutil

    shutil.rmtree(shard)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("safe")
    shard.symlink_to(outside, target_is_directory=True)
    with pytest.raises(LinkInTheWay):
        export_shards(home, home / "shards", entries=["mcp"])
    assert (outside / "keep").read_text() == "safe"


def test_concurrent_resolution_refusal_keeps_queue_open(tmp_path, monkeypatch):
    from gideon.operations.durability import conflicts
    from gideon.operations.durability import inventory as inv
    from gideon.operations.durability import writeback
    from gideon.operations.durability.conflict_resolve import resolve_conflict

    home = tmp_path / "home"
    folder = home / "tasks"
    folder.mkdir(parents=True)
    path = folder / "note.md"
    path.write_text("local")
    record = conflicts.ConflictRecord(
        entry_id="tasks",
        entity_id="note.md",
        domain=inv.DOMAIN_WORK,
        surface=conflicts.SURFACE_DURABILITY,
        ancestor_sha="",
        local_sha="l",
        remote_sha="r",
        local_row={"id": "note.md", "text": "local"},
        remote_row={"id": "note.md", "text": "remote"},
    )
    queue = conflicts.ConflictQueue(home)
    queue.record(record)
    actual_apply = writeback.apply_rows

    def interleaved_apply(*args, **kwargs):
        # Place an actual writer's edit after resolution read and before the real guarded apply.
        path.write_text("new local writer")
        return actual_apply(*args, **kwargs)

    monkeypatch.setattr(writeback, "apply_rows", interleaved_apply)
    result = resolve_conflict(home, record.id, "take_remote")
    assert not result.ok and result.code == "changed_since_read"
    assert path.read_text() == "new local writer"
    assert queue.get(record.id).status == conflicts.STATUS_NEEDS_REVIEW
