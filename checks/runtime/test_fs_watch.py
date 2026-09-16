"""Tests for the config-tree FS watcher."""

from __future__ import annotations

import os

from gideon.automation.fs_watch import FS_WATCH_FEED, ConfigFsWatcher, _signature


def test_first_scan_seeds_baseline_no_storm(tmp_path):
    """The first pass only records signatures — never reports a change."""
    (tmp_path / "config.json").write_text('{"a": 1}')
    (tmp_path / "agent.md").write_text("hello")
    events: list = []
    w = ConfigFsWatcher([tmp_path], publish=lambda *a: events.append(a))
    assert w.scan_once() == []
    assert events == []


def test_detects_modified_file(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"a": 1}')
    events: list = []
    w = ConfigFsWatcher([tmp_path], publish=lambda *a: events.append(a))
    w.scan_once()
    prior = cfg.stat()
    cfg.write_text('{"a": 2}')
    os.utime(cfg, ns=(prior.st_atime_ns, prior.st_mtime_ns + 1_000_000_000))
    changed = w.scan_once()
    assert str(cfg) in changed
    assert events and events[0][0] == FS_WATCH_FEED
    assert events[0][1] == "changed"
    assert events[0][2] == {"path": str(cfg)}


def test_detects_new_file(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    w = ConfigFsWatcher([tmp_path], publish=None)
    w.scan_once()
    new = tmp_path / "agents" / "x.md"
    new.parent.mkdir()
    new.write_text("new agent")
    changed = w.scan_once()
    assert str(new) in changed


def test_detects_deletion(tmp_path):
    f = tmp_path / "skill.md"
    f.write_text("body")
    w = ConfigFsWatcher([tmp_path], publish=None)
    w.scan_once()
    f.unlink()
    changed = w.scan_once()
    assert str(f) in changed


def test_suffix_filter_ignores_unwatched(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    w = ConfigFsWatcher([tmp_path], publish=None)
    w.scan_once()
    (tmp_path / "scratch.bin").write_bytes(b"\x00\x01")
    assert w.scan_once() == []


def test_signature_missing_file_is_sentinel(tmp_path):
    assert _signature(tmp_path / "nope.json") == (0.0, -1)


def test_publish_failure_never_raises(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{}")

    def _boom(*_a):
        raise RuntimeError("sse down")

    w = ConfigFsWatcher([tmp_path], publish=_boom)
    w.scan_once()
    cfg.write_text('{"x": 1}')
    assert str(cfg) in w.scan_once()


async def test_polling_task_stops_and_restarts_with_same_baseline(tmp_path):
    import asyncio

    path = tmp_path / "config.json"
    path.write_text("{}")
    events = asyncio.Queue()
    watcher = ConfigFsWatcher(
        [tmp_path],
        interval=0.01,
        publish=lambda feed, kind, body: events.put_nowait((feed, kind, body)),
    )
    watcher.scan_once()
    watcher.start()
    first = watcher._task
    watcher.start()
    assert watcher._task is first
    path.write_text('{"version": 1}')
    assert await asyncio.wait_for(events.get(), 1) == (
        FS_WATCH_FEED,
        "changed",
        {"path": str(path)},
    )
    watcher.stop()
    try:
        await first
    except asyncio.CancelledError:
        pass
    assert first.done()
    path.unlink()
    watcher.start()
    second = watcher._task
    try:
        assert second is not first
        assert await asyncio.wait_for(events.get(), 1) == (
            FS_WATCH_FEED,
            "changed",
            {"path": str(path)},
        )
    finally:
        watcher.stop()
        try:
            await second
        except asyncio.CancelledError:
            pass


def test_explicit_file_and_overlapping_roots_preserve_observation_order(tmp_path):
    path = tmp_path / "explicit.bin"
    path.write_bytes(b"initial")
    watcher = ConfigFsWatcher([path, path])
    assert watcher.scan_once() == []
    path.write_bytes(b"changed length")
    assert watcher.scan_once() == [str(path), str(path)]
    path.unlink()
    assert watcher.scan_once() == [str(path)]
