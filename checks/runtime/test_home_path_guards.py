"""Real linked-path refusal before sync and archive landing writes."""

import os
from pathlib import Path

import pytest

from gideon.operations.durability.home_paths import LinkInTheWay, home_path, open_lock
from gideon.operations.durability.sqlite_files import bring_in, copy_file
from gideon.operations.durability.writeback import apply_rows


def test_nested_link_refused_before_directory_creation(tmp_path):
    home, outside = tmp_path / "home", tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    (home / "store").symlink_to(outside, target_is_directory=True)
    with pytest.raises(LinkInTheWay):
        apply_rows(
            "json_entity_dir", home / "store" / "new", [{"id": "x", "data": {"v": 1}}]
        )
    assert list(outside.iterdir()) == []


def test_hard_link_and_sidecar_refusal_changes_no_bytes_or_modes(tmp_path):
    source, outside, target = (
        tmp_path / "source",
        tmp_path / "outside",
        tmp_path / "target",
    )
    source.write_bytes(b"incoming")
    outside.write_bytes(b"original")
    outside.chmod(0o644)
    os.link(outside, target)
    with pytest.raises(LinkInTheWay):
        copy_file(source, target)
    assert outside.read_bytes() == b"original"
    assert outside.stat().st_mode & 0o777 == 0o644
    target.unlink()
    (tmp_path / "target-wal").symlink_to(outside)
    with pytest.raises(LinkInTheWay):
        bring_in(source, target)
    assert not target.exists()
    assert outside.read_bytes() == b"original"


def test_record_lock_hard_link_is_refused_before_new_record(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("keep")
    outside.chmod(0o644)
    os.link(outside, root / ".gideon-record-files.lock")
    with pytest.raises(LinkInTheWay):
        apply_rows("json_entity_dir", root, [{"id": "x", "data": {}}])
    assert not (root / "x.json").exists()
    assert outside.read_text() == "keep"
    assert outside.stat().st_mode & 0o777 == 0o644


def test_restore_preflight_refuses_before_displacement(tmp_path):
    from gideon.workspace.snapshot import _do_replace

    incoming, home, outside = (
        tmp_path / "incoming",
        tmp_path / "home",
        tmp_path / "outside",
    )
    incoming.mkdir()
    home.mkdir()
    outside.mkdir()
    (incoming / "config.json").write_text("{}")
    (home / "config.json").symlink_to(outside / "config.json")
    left = _do_replace(incoming, home, ["config"])
    assert left and "config.json" in left[0]
    assert not list(home.glob("pre-restore-*"))
    assert not (outside / "config.json").exists()


def test_safe_copy_and_private_lock_still_work(tmp_path):
    source, dest = tmp_path / "source", tmp_path / "folder" / "target"
    source.write_text("ok")
    copy_file(source, dest)
    assert dest.read_text() == "ok"
    with open_lock(tmp_path / "lock") as lock:
        assert lock.read() == b""
    assert (tmp_path / "lock").stat().st_mode & 0o777 == 0o600


def test_path_traversal_rejected(tmp_path):
    for path in ("../x", "a/../b", "a\\b", "/outside", "a//b"):
        with pytest.raises(ValueError):
            home_path(tmp_path, path)
